"""
Tests persistence.Persistence + persistence.DBServer against the local PostgreSQL TEST database
(ashok_leyland_test) and Redis. Run from the project root:  python -m test.Test_DBServer_And_Persistence
(or: python test/Test_DBServer_And_Persistence.py). Needs PostgreSQL on 127.0.0.1:5432 (postgres/postgres)
and Redis on the configured host/port. Images are written under the configured base image folder, relative
to the current working directory on non-Windows hosts, so run it from a scratch directory if you do not want them there.

This script tests (Machine 2 record layout - no free-rotations-done fields):
1. Database connection, schema/table creation, test role + user
2. Direct insertions using Persistence.insertData()
3. Duplicate QR code handling
4. Redis connection
5. Starting DBServer (DataPersistence) in Test mode
6. Redis-based insertions using sendDataFromFEServerToDatabaseServer() -> fe2db.q -> DBServer -> PostgreSQL
7. DateTime handling
8. Data retrieval and validation
9. Torque value storage (tightening torque 1/2, free rotation torque 1)
10. Image file creation and storage (files written by DBServer for Redis records)
11. Cleanup of test records
"""

import sys
import os
import time
import threading
from datetime import datetime
from typing import Optional, List, Dict, Tuple

# Make the project root importable regardless of how this file is launched
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import numpy as np
import cv2

import redis
import psycopg2

from persistence.DBServer import DataPersistence
from persistence.Persistence import (
    insertRole,
    insertNewUser,
    insertData,
    checkIfRecordExists,
    checkIfQRCodeExists,
    getDatabaseName,
    setDatabaseName,
    createSchema,
    createAllTables,
    getTodaysDateAsAFolder
)
from utils.RedisUtils import (
    sendDataFromFEServerToDatabaseServer,
    readDataInDatabaseServerFromFEServer,
    clearQueues
)
from utils.QRCodeHelper import getModel_LHSRHS_AndTonnage
from BaseUtils import getCurrentTime, getPostgresDatetimeFromString
from utils.CosThetaPrintUtils import *
from Configuration import CosThetaConfigurator

# =============================================================================
# Test Configuration
# =============================================================================

class TestConfig:
    """Configuration for test execution."""

    def __init__(self):
        self.test_db_name = "test_database"
        self.test_mode = "Test"
        self.test_username = "test_user"
        self.redis_host = CosThetaConfigurator.getInstance().getRedisHost()
        self.redis_port = CosThetaConfigurator.getInstance().getRedisPort()
        # QR codes start with the real part-number prefixes (qrcode.partmapping.N in ApplicationConfiguration.properties)
        # so that utils.QRCodeHelper.getModel_LHSRHS_AndTonnage() resolves model / side / tonnage; the
        # "$TESTQR..." suffix keeps them unique and recognisable for cleanup.
        self.test_qr_codes = [
            "7204838$400112VA1D$TESTQR001$",     # DOST     LHS 2.0T
            "7204838$400102VA1D$TESTQR002$",     # DOST     RHS 2.0T
            "8201206$400112VA1C$TESTQR003$",     # DOSTPLUS LHS 2.5T
            "8201206$400102VA1C$TESTQR004$",     # DOSTPLUS RHS 2.5T
            "7204838$400112VA1D$TESTQR005$",     # For Redis test
            "8201206$400102VA1C$TESTQR006$"      # For Redis test
        ]
        self.expected_models = {
            qr: getModel_LHSRHS_AndTonnage(qr) for qr in self.test_qr_codes
        }
        self.db_server = None
        self.redis_connection = None
        self.test_results = {
            'passed': 0,
            'failed': 0,
            'skipped': 0
        }


# =============================================================================
# Test Utilities
# =============================================================================

def print_test_header(test_name: str) -> None:
    """Print formatted test header."""
    printBoldBlue("=" * 80)
    printBoldBlue(f"TEST: {test_name}")
    printBoldBlue("=" * 80)


def print_test_result(test_name: str, passed: bool, message: str = "") -> None:
    """Print formatted test result."""
    if passed:
        printBoldGreen(f"✓ PASSED: {test_name}")
        if message:
            printBoldBlue(f"  {message}")
    else:
        printBoldRed(f"✗ FAILED: {test_name}")
        if message:
            printBoldRed(f"  {message}")


def create_test_image(width: int = 640, height: int = 480, color: Tuple[int, int, int] = (100, 150, 200)) -> np.ndarray:
    """
    Create a test image with timestamp text.

    Args:
        width: Image width
        height: Image height
        color: BGR color tuple

    Returns:
        numpy array representing the image
    """
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:] = color

    # Add timestamp text
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cv2.putText(image, f"Test Image - {timestamp}", (10, height // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

    return image


def verify_record_in_database(qr_code: str, db_name: str) -> bool:
    """
    Verify a record exists in the database.

    Args:
        qr_code: QR code to check
        db_name: Database name

    Returns:
        bool: True if record exists
    """
    try:
        # Use parameterized query to prevent SQL injection
        query = "SELECT qr_code FROM al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data WHERE qr_code = %s"

        conn = psycopg2.connect(
            user="postgres",
            password="postgres",
            host="127.0.0.1",
            port="5432",
            database=db_name
        )
        cur = conn.cursor()
        cur.execute(query, [qr_code])
        result = cur.fetchone()
        cur.close()
        conn.close()

        return result is not None

    except Exception as e:
        printBoldRed(f"Error verifying record: {e}")
        return False


def get_record_from_database(qr_code: str, db_name: str) -> Optional[Dict]:
    """
    Retrieve a record from the database.

    Args:
        qr_code: QR code to retrieve
        db_name: Database name

    Returns:
        Dictionary with record data, or None if not found
    """
    conn = None
    cur = None

    try:
        conn = psycopg2.connect(
            user="postgres",
            password="postgres",
            host="127.0.0.1",
            port="5432",
            database=db_name
        )
        cur = conn.cursor()

        query = """
            SELECT qr_code, model_name, lhs_rhs, model_tonnage,
                   check_knuckle_result, check_hub_and_bottom_bearing_result,
                   nut_tightening_torque_1, ok_notok_result, created_on,
                   component_assembly_start_datetime,
                   nut_tightening_torque_2, free_rotation_torque_1, free_rotation_torque_1_result,
                   remarks,
                   check_knuckle_imagefile, check_hub_and_bottom_bearing_imagefile, check_top_bearing_imagefile,
                   check_nut_and_platewasher_imagefile, check_bunk_for_component_press_imagefile,
                   check_no_bunk_imagefile, check_splitpin_and_washer_imagefile, check_cap_imagefile,
                   check_bunk_cap_press_imagefile
            FROM al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data
            WHERE qr_code = %s
            ORDER BY record_id ASC
            LIMIT 1
        """
        cur.execute(query, [qr_code])
        row = cur.fetchone()

        if row:
            return {
                'qr_code': row[0],
                'model_name': row[1],
                'lhs_rhs': row[2],
                'model_tonnage': row[3],
                'check_knuckle_result': row[4],
                'check_hub_and_bottom_bearing_result': row[5],
                'nut_tightening_torque_1': row[6],
                'ok_notok_result': row[7],
                'created_on': row[8],
                'component_assembly_start_datetime': row[9],
                'nut_tightening_torque_2': row[10],
                'free_rotation_torque_1': row[11],
                'free_rotation_torque_1_result': row[12],
                'remarks': row[13],
                'imagefiles': {
                    'knuckle': row[14], 'hub_and_bottom_bearing': row[15], 'top_bearing': row[16],
                    'nut_and_platewasher': row[17], 'bunk_for_component_press': row[18], 'no_bunk': row[19],
                    'splitpin_and_washer': row[20], 'cap': row[21], 'bunk_for_cap_press': row[22],
                }
            }

        return None

    except Exception as e:
        printBoldRed(f"Error retrieving record: {e}")
        return None

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def count_records_in_database(qr_code: str, db_name: str) -> Tuple[int, List[str]]:
    """Return (number of rows, list of remarks) for a QR code (qr_code is NOT unique in the schema)."""
    conn = None
    try:
        conn = psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1", port="5432", database=db_name)
        cur = conn.cursor()
        cur.execute("SELECT remarks FROM al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data "
                    "WHERE qr_code = %s ORDER BY record_id", [qr_code])
        rows = cur.fetchall()
        cur.close()
        return len(rows), [r[0] for r in rows]
    except Exception as e:
        printBoldRed(f"Error counting records: {e}")
        return -1, []
    finally:
        if conn:
            conn.close()


def wait_for_record_count(qr_code: str, db_name: str, expected: int, timeout_s: float = 15.0) -> int:
    """Poll until the QR code has `expected` rows (DBServer polls fe2db.q only every 5 s)."""
    t0 = time.time()
    n = 0
    while time.time() - t0 < timeout_s:
        n, _ = count_records_in_database(qr_code, db_name)
        if n >= expected:
            return n
        time.sleep(0.5)
    return n


def clean_test_data(qr_codes: List[str], db_name: str) -> None:
    """
    Delete test records from database.

    Args:
        qr_codes: List of QR codes to delete
        db_name: Database name
    """
    conn = None
    cur = None

    try:
        conn = psycopg2.connect(
            user="postgres",
            password="postgres",
            host="127.0.0.1",
            port="5432",
            database=db_name
        )
        conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()

        for qr_code in qr_codes:
            try:
                cur.execute(
                    "DELETE FROM al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data WHERE qr_code = %s",
                    [qr_code]
                )
                printBoldBlue(f"Deleted test record: {qr_code}")
            except Exception as e:
                printBoldYellow(f"Could not delete {qr_code}: {e}")

    except Exception as e:
        printBoldRed(f"Error cleaning test data: {e}")

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# =============================================================================
# Test Cases
# =============================================================================

def test_01_database_connection(config: TestConfig) -> bool:
    """Test 1: Database Connection and Setup."""
    print_test_header("Database Connection and Setup")

    try:
        # Set database name to Test mode
        db_name = setDatabaseName(config.test_mode, createDB=True)
        printBoldGreen(f"✓ Database set to: {db_name}")

        # Verify database name
        retrieved_name = getDatabaseName()
        if retrieved_name != db_name:
            print_test_result("Database Name Verification", False,
                            f"Expected {db_name}, got {retrieved_name}")
            return False

        printBoldGreen(f"✓ Database name verified: {retrieved_name}")

        # Create schema and tables
        createSchema(db_name)
        printBoldGreen(f"✓ Schema created")

        createAllTables(db_name)
        # Remove leftovers of an earlier, aborted run so that counts below are exact
        clean_test_data(config.test_qr_codes, db_name)
        # Add test role and user
        insertRole(role_name="Operator", db_name=db_name, module_access="")
        insertNewUser(
            db_name=db_name,
            username="test_user",
            password="test_password",
            first_name="Test",
            middle_name="",
            last_name="User",
            role_name="Operator",
            email="test@example.com",
            mobile="0000000000",
            remarks="Test user for automated testing"
        )
        printBoldGreen(f"✓ Test user created")
        printBoldGreen(f"✓ Tables created")

        print_test_result("Database Connection and Setup", True,
                         f"Database: {db_name}")
        config.test_results['passed'] += 1
        return True

    except Exception as e:
        print_test_result("Database Connection and Setup", False, str(e))
        config.test_results['failed'] += 1
        import traceback
        traceback.print_exc()
        return False


def test_02_direct_insertions(config: TestConfig) -> bool:
    """Test 2: Direct Insertions using Persistence.insertData()."""
    print_test_header("Direct Insertions using Persistence.insertData()")

    db_name = getDatabaseName()
    test_data = []

    # Prepare 4 test records
    for i in range(4):
        qr_code = config.test_qr_codes[i]

        # Create test images
        knuckle_img = create_test_image(color=(100, 100, 200))
        hub_img = create_test_image(color=(100, 200, 100))
        top_bearing_img = create_test_image(color=(200, 100, 100))
        nut_img = create_test_image(color=(150, 150, 100))

        # Prepare data
        data = {
            'qr_code': qr_code,
            'knuckle_result': 'OK' if i % 2 == 0 else 'NotOK',
            'knuckle_datetime': getCurrentTime(),
            'hub_result': 'OK',
            'hub_datetime': getCurrentTime(),
            'top_bearing_result': 'OK',
            'top_bearing_datetime': getCurrentTime(),
            'nut_result': 'OK',
            'nut_datetime': getCurrentTime(),
            'torque1': 45.5 + i,
            'torque1_result': 'OK',
            'torque1_datetime': getCurrentTime(),
            'torque2': 50.0 + i,
            'torque2_result': 'OK',
            'torque2_datetime': getCurrentTime(),
            'free_rotation_torque': 5.5 + i,
            'free_rotation_result': 'OK',
            'free_rotation_datetime': getCurrentTime(),
            'overall_result': 'OK' if i % 2 == 0 else 'NotOK'
        }

        test_data.append(data)

    # Insert records
    inserted_count = 0
    failed_count = 0

    for i, data in enumerate(test_data):
        printBoldBlue(f"\nInserting record {i+1}/4: {data['qr_code']}")

        try:
            # Parse QR code to get model, lhs_rhs, tonnage (same helper DBServer uses)
            model_name, lhs_rhs, tonnage = getModel_LHSRHS_AndTonnage(data['qr_code'])

            # FIXED: db_name is now the FIRST parameter!
            success = insertData(
                db_name=db_name,  # FIRST!
                qr_code=data['qr_code'],
                model_name=model_name,
                lhs_rhs=lhs_rhs,
                model_tonnage=tonnage,
                component_assembly_start_datetime=getCurrentTime(),
                check_knuckle_imagefile='',
                check_knuckle_result=data['knuckle_result'],
                check_knuckle_datetime=data['knuckle_datetime'],
                check_hub_and_bottom_bearing_imagefile='',
                check_hub_and_bottom_bearing_result=data['hub_result'],
                check_hub_and_bottom_bearing_datetime=data['hub_datetime'],
                check_top_bearing_imagefile='',
                check_top_bearing_result=data['top_bearing_result'],
                check_top_bearing_datetime=data['top_bearing_datetime'],
                check_nut_and_platewasher_imagefile='',
                check_nut_and_platewasher_result=data['nut_result'],
                check_nut_and_platewasher_datetime=data['nut_datetime'],
                nut_tightening_torque_1=data['torque1'],
                nut_tightening_torque_1_result=data['torque1_result'],
                nut_tightening_torque_1_datetime=data['torque1_datetime'],
                check_bunk_for_component_press_imagefile='',
                check_bunk_for_component_press_result='OK',
                check_bunk_for_component_press_datetime=getCurrentTime(),
                component_press_done='Done',
                component_press_datetime=getCurrentTime(),
                check_no_bunk_imagefile='',
                check_no_bunk_result='OK',
                check_no_bunk_datetime=getCurrentTime(),
                nut_tightening_torque_2=data['torque2'],
                nut_tightening_torque_2_result=data['torque2_result'],
                nut_tightening_torque_2_datetime=data['torque2_datetime'],
                check_splitpin_and_washer_imagefile='',
                check_splitpin_and_washer_result='OK',
                check_splitpin_and_washer_datetime=getCurrentTime(),
                check_cap_imagefile='',
                check_cap_result='OK',
                check_cap_datetime=getCurrentTime(),
                check_bunk_cap_press_imagefile='',
                check_bunk_cap_press_result='OK',
                check_bunk_cap_press_datetime=getCurrentTime(),
                cap_press_done='Done',
                cap_press_datetime=getCurrentTime(),
                free_rotation_torque_1=data['free_rotation_torque'],
                free_rotation_torque_1_result=data['free_rotation_result'],
                free_rotation_torque_1_datetime=data['free_rotation_datetime'],
                ok_notok_result=data['overall_result'],
                username='test_user',
                remarks=f'Test record {i+1}'
            )

            if success:
                printBoldGreen(f"✓ Inserted: {data['qr_code']}")
                inserted_count += 1

                # Verify insertion
                if verify_record_in_database(data['qr_code'], db_name):
                    printBoldGreen(f"✓ Verified in database: {data['qr_code']}")
                else:
                    printBoldYellow(f"⚠ Could not verify: {data['qr_code']}")
            else:
                printBoldRed(f"✗ Failed to insert: {data['qr_code']}")
                failed_count += 1

        except Exception as e:
            printBoldRed(f"✗ Error inserting {data['qr_code']}: {e}")
            import traceback
            traceback.print_exc()
            failed_count += 1

    # Test result
    success = (inserted_count == 4 and failed_count == 0)
    print_test_result("Direct Insertions", success,
                     f"Inserted: {inserted_count}/4, Failed: {failed_count}")

    if success:
        config.test_results['passed'] += 1
    else:
        config.test_results['failed'] += 1

    return success


def test_03_duplicate_qr_handling(config: TestConfig) -> bool:
    """Test 3: Duplicate QR Code Handling.

    qr_code is deliberately NOT unique in hub_and_disc_assembly_data: a re-worked component is stored again.
    checkIfQRCodeExists() must report the existing record, and insertData() must store the second row
    (DBServer additionally sets remarks='DUPLICATE' - verified in the Redis test).
    """
    print_test_header("Duplicate QR Code Handling")

    db_name = getDatabaseName()
    duplicate_qr = config.test_qr_codes[0]  # Use first QR code again

    try:
        exists = checkIfQRCodeExists(qr_code=duplicate_qr, db_name=db_name)
        printBoldBlue(f"checkIfQRCodeExists({duplicate_qr}) -> {exists}")
        before, _ = count_records_in_database(duplicate_qr, db_name)

        printBoldBlue(f"Attempting to insert duplicate QR: {duplicate_qr}")
        model_name, lhs_rhs, tonnage = getModel_LHSRHS_AndTonnage(duplicate_qr)
        success = insertData(
            db_name=db_name,
            qr_code=duplicate_qr,
            model_name=model_name,
            lhs_rhs=lhs_rhs,
            model_tonnage=tonnage,
            component_assembly_start_datetime=getCurrentTime(),
            check_knuckle_imagefile='',
            check_knuckle_result='OK',
            check_knuckle_datetime=getCurrentTime(),
            ok_notok_result='OK',
            username='test_user',
            remarks='DUPLICATE'
        )
        after, remarks = count_records_in_database(duplicate_qr, db_name)
        printBoldBlue(f"insertData -> {success}; rows for QR before={before}, after={after}; remarks={remarks}")

        passed = exists and success and (after == before + 1)
        print_test_result("Duplicate QR Code Handling", passed,
                          "Existing QR detected and re-worked component stored as a second row" if passed
                          else f"exists={exists}, inserted={success}, rows {before}->{after}")
        config.test_results['passed' if passed else 'failed'] += 1
        return passed

    except Exception as e:
        printBoldRed(f"Error in duplicate test: {e}")
        print_test_result("Duplicate QR Code Handling", False, str(e))
        config.test_results['failed'] += 1
        import traceback
        traceback.print_exc()
        return False


def test_04_redis_connection(config: TestConfig) -> bool:
    """Test 4: Redis Connection."""
    print_test_header("Redis Connection")

    try:
        config.redis_connection = redis.Redis(
            host=config.redis_host,
            port=config.redis_port,
            retry_on_timeout=True
        )

        # Test connection
        config.redis_connection.ping()
        printBoldGreen(f"✓ Connected to Redis at {config.redis_host}:{config.redis_port}")

        # Clear queues
        clearQueues(config.redis_connection, reportCounts=False)
        printBoldGreen(f"✓ Redis queues cleared")

        print_test_result("Redis Connection", True,
                         f"Connected to {config.redis_host}:{config.redis_port}")
        config.test_results['passed'] += 1
        return True

    except Exception as e:
        printBoldRed(f"Redis connection failed: {e}")
        print_test_result("Redis Connection", False, str(e))
        config.test_results['failed'] += 1
        return False


def test_05_start_dbserver(config: TestConfig) -> bool:
    """Test 5: Start DBServer in Test Mode."""
    print_test_header("Start DBServer in Test Mode")

    try:
        if config.redis_connection is None:
            printBoldRed("Redis connection not available")
            print_test_result("Start DBServer", False, "No Redis connection")
            config.test_results['failed'] += 1
            return False

        # FIXED: Create DBServer with mode and username
        printBoldBlue(f"Creating DBServer with mode='{config.test_mode}', username='{config.test_username}'")
        config.db_server = DataPersistence(mode=config.test_mode, username=config.test_username)
        printBoldGreen(f"✓ DBServer instance created")

        # DBServer automatically starts threads in __init__
        printBoldGreen(f"✓ DBServer threads started automatically")

        # Give it a moment to initialize
        time.sleep(2)

        print_test_result("Start DBServer", True, "DBServer running in Test mode")
        config.test_results['passed'] += 1
        return True

    except Exception as e:
        printBoldRed(f"Failed to start DBServer: {e}")
        print_test_result("Start DBServer", False, str(e))
        config.test_results['failed'] += 1
        import traceback
        traceback.print_exc()
        return False


def test_06_redis_insertions(config: TestConfig) -> bool:
    """Test 6: Insertions via Redis using sendDataFromFEServerToDatabaseServer()."""
    print_test_header("Redis-based Insertions")

    if config.redis_connection is None or config.db_server is None:
        printBoldRed("Redis or DBServer not available")
        print_test_result("Redis Insertions", False, "Prerequisites not met")
        config.test_results['failed'] += 1
        return False

    db_name = getDatabaseName()
    inserted_count = 0
    failed_count = 0

    # Send 2 records via Redis
    for i in range(2):
        qr_code = config.test_qr_codes[4 + i]  # Use QR codes 5 and 6

        printBoldBlue(f"\nSending record {i+1}/2 via Redis: {qr_code}")

        # Create test images
        knuckle_img = create_test_image(color=(200, 100, 150))
        hub_img = create_test_image(color=(150, 200, 100))
        top_bearing_img = create_test_image(color=(100, 150, 200))
        nut_img = create_test_image(color=(200, 200, 100))
        split_pin_img = create_test_image(color=(100, 200, 200))
        cap_img = create_test_image(color=(200, 100, 100))
        bung_img = create_test_image(color=(150, 150, 150))

        try:
            # Send via Redis
            success = sendDataFromFEServerToDatabaseServer(
                redisConnection=config.redis_connection,
                qrCode=qr_code,
                knucklePicture=knuckle_img,
                knuckleResult='OK',
                knuckleDatetime=getCurrentTime(),
                hubAndBottomBearingPicture=hub_img,
                hubAndBottomBearingResult='OK',
                hubAndBottomBearingDatetime=getCurrentTime(),
                topBearingPicture=top_bearing_img,
                topBearingResult='OK',
                topBearingDatetime=getCurrentTime(),
                nutAndPlateWasherPicture=nut_img,
                nutAndPlateWasherResult='OK',
                nutAndPlateWasherDatetime=getCurrentTime(),
                tighteningTorque1=47.5 + i,
                tighteningTorque1Result='OK',
                tighteningTorque1Datetime=getCurrentTime(),
                componentPressBunkCheckingPicture=bung_img,
                componentPressBunkCheckingResult='OK',
                componentPressBunkCheckDatetime=getCurrentTime(),
                componentPressDone='Done',
                componentPressDoneDatetime=getCurrentTime(),
                noBunkCheckingPicture=bung_img,
                noBunkCheckingResult='OK',
                noBunkCheckDatetime=getCurrentTime(),
                tighteningTorque2=52.0 + i,
                tighteningTorque2Result='OK',
                tighteningTorque2Datetime=getCurrentTime(),
                splitPinAndWasherPicture=split_pin_img,
                splitPinAndWasherResult='OK',
                splitPinAndWasherDatetime=getCurrentTime(),
                capCheckingPicture=cap_img,
                capCheckingResult='OK',
                capCheckingDatetime=getCurrentTime(),
                bunkCheckingPicture=bung_img,
                capPressBunkCheckingResult='OK',
                capPressBunkCheckDatetime=getCurrentTime(),
                pressDone='Done',
                capPressDoneDatetime=getCurrentTime(),
                freeRotationTorque1=6.5 + i,
                freeRotationTorque1Result='OK',
                freeRotationTorque1Datetime=getCurrentTime(),
                overallResult='OK',
                aProducer='TestScript'
            )

            if success:
                printBoldGreen(f"✓ Sent to Redis: {qr_code}")

                # Wait for DBServer to process. DBServer polls fe2db.q every 5 s and trims the queue to the
                # latest message, so each record must be consumed before the next one is sent.
                printBoldBlue("Waiting for DBServer to process...")
                n = wait_for_record_count(qr_code, db_name, expected=1, timeout_s=15.0)

                if n >= 1:
                    printBoldGreen(f"✓ Verified in database: {qr_code}")
                    record = get_record_from_database(qr_code, db_name)
                    expected = config.expected_models[qr_code]
                    if record:
                        printBoldBlue(f"  Model: {record['model_name']}, LHS/RHS: {record['lhs_rhs']}, "
                                      f"Tonnage: {record['model_tonnage']}")
                        printBoldBlue(f"  Torque1: {record['nut_tightening_torque_1']}, "
                                      f"Torque2: {record['nut_tightening_torque_2']}, "
                                      f"FreeRotationTorque1: {record['free_rotation_torque_1']}")
                        printBoldBlue(f"  Result: {record['ok_notok_result']}")
                    if record and (record['model_name'], record['lhs_rhs'], record['model_tonnage']) == expected:
                        inserted_count += 1
                    else:
                        printBoldRed(f"✗ Model/side/tonnage mismatch for {qr_code}: expected {expected}")
                        failed_count += 1
                else:
                    printBoldYellow(f"⚠ Not found in database after 15 seconds: {qr_code}")
                    failed_count += 1
            else:
                printBoldRed(f"✗ Failed to send to Redis: {qr_code}")
                failed_count += 1

        except Exception as e:
            printBoldRed(f"✗ Error in Redis insertion: {e}")
            import traceback
            traceback.print_exc()
            failed_count += 1

    # Re-send the first Redis QR code: DBServer must store a second row marked remarks='DUPLICATE'
    dup_qr = config.test_qr_codes[4]
    printBoldBlue(f"\nRe-sending {dup_qr} via Redis (duplicate)")
    sendDataFromFEServerToDatabaseServer(
        redisConnection=config.redis_connection, qrCode=dup_qr,
        knucklePicture=None, knuckleResult='OK', knuckleDatetime=getCurrentTime(),
        hubAndBottomBearingPicture=None, hubAndBottomBearingResult='OK', hubAndBottomBearingDatetime=getCurrentTime(),
        topBearingPicture=None, topBearingResult='OK', topBearingDatetime=getCurrentTime(),
        nutAndPlateWasherPicture=None, nutAndPlateWasherResult='OK', nutAndPlateWasherDatetime=getCurrentTime(),
        tighteningTorque1=47.0, tighteningTorque1Result='OK', tighteningTorque1Datetime=getCurrentTime(),
        componentPressBunkCheckingPicture=None, componentPressBunkCheckingResult='OK', componentPressBunkCheckDatetime=getCurrentTime(),
        componentPressDone='Done', componentPressDoneDatetime=getCurrentTime(),
        noBunkCheckingPicture=None, noBunkCheckingResult='OK', noBunkCheckDatetime=getCurrentTime(),
        tighteningTorque2=51.0, tighteningTorque2Result='OK', tighteningTorque2Datetime=getCurrentTime(),
        splitPinAndWasherPicture=None, splitPinAndWasherResult='OK', splitPinAndWasherDatetime=getCurrentTime(),
        capCheckingPicture=None, capCheckingResult='OK', capCheckingDatetime=getCurrentTime(),
        bunkCheckingPicture=None, capPressBunkCheckingResult='OK', capPressBunkCheckDatetime=getCurrentTime(),
        pressDone='Done', capPressDoneDatetime=getCurrentTime(),
        freeRotationTorque1=6.0, freeRotationTorque1Result='OK', freeRotationTorque1Datetime=getCurrentTime(),
        overallResult='OK', aProducer='TestScript')
    n = wait_for_record_count(dup_qr, db_name, expected=2, timeout_s=15.0)
    _, remarks = count_records_in_database(dup_qr, db_name)
    if n == 2 and remarks[-1] == 'DUPLICATE':
        printBoldGreen(f"✓ Duplicate stored as second row with remarks={remarks}")
    else:
        printBoldRed(f"✗ Duplicate handling via DBServer: rows={n}, remarks={remarks}")
        failed_count += 1

    # Test result
    success = (inserted_count == 2 and failed_count == 0)
    print_test_result("Redis Insertions", success,
                     f"Inserted: {inserted_count}/2 (+1 duplicate), Failed: {failed_count}")

    if success:
        config.test_results['passed'] += 1
    else:
        config.test_results['failed'] += 1

    return success


def test_07_datetime_handling(config: TestConfig) -> bool:
    """Test 7: DateTime Format Handling."""
    print_test_header("DateTime Format Handling")

    db_name = getDatabaseName()

    try:
        # Get a record we inserted
        test_qr = config.test_qr_codes[0]
        record = get_record_from_database(test_qr, db_name)

        if not record:
            printBoldRed(f"Could not retrieve record: {test_qr}")
            print_test_result("DateTime Handling", False, "Record not found")
            config.test_results['failed'] += 1
            return False

        # Check both datetime fields
        created_on = record['created_on']
        assembly_start = record['component_assembly_start_datetime']

        printBoldBlue(f"Created on: {created_on}")
        printBoldBlue(f"Assembly start: {assembly_start}")

        if created_on is not None and assembly_start is not None:
            printBoldGreen(f"✓ DateTime fields stored successfully")

            # Verify they're valid datetimes
            if isinstance(created_on, datetime) and isinstance(assembly_start, datetime):
                printBoldGreen(f"✓ DateTime types are correct")
                print_test_result("DateTime Handling", True,
                                f"Created: {created_on}, Assembly: {assembly_start}")
                config.test_results['passed'] += 1
                return True
            else:
                printBoldYellow(f"⚠ DateTime types unexpected")
                print_test_result("DateTime Handling", False, "Wrong types")
                config.test_results['failed'] += 1
                return False
        else:
            printBoldRed(f"✗ One or both DateTimes are NULL")
            printBoldRed(f"  created_on: {created_on}")
            printBoldRed(f"  assembly_start: {assembly_start}")
            print_test_result("DateTime Handling", False, "DateTime is NULL")
            config.test_results['failed'] += 1
            return False

    except Exception as e:
        printBoldRed(f"Error testing datetime: {e}")
        print_test_result("DateTime Handling", False, str(e))
        config.test_results['failed'] += 1
        import traceback
        traceback.print_exc()
        return False


def test_08_data_retrieval(config: TestConfig) -> bool:
    """Test 8: Data Retrieval and Validation."""
    print_test_header("Data Retrieval and Validation")

    db_name = getDatabaseName()
    retrieved_count = 0

    try:
        printBoldBlue("Retrieving all test records...")

        for qr_code in config.test_qr_codes[:6]:  # Check first 6 QR codes
            record = get_record_from_database(qr_code, db_name)

            if record:
                printBoldGreen(f"✓ Retrieved: {qr_code}")
                printBoldBlue(f"  Model: {record['model_name']}, "
                            f"Result: {record['ok_notok_result']}, "
                            f"Created: {record['created_on']}")
                retrieved_count += 1
            else:
                printBoldYellow(f"⚠ Not found: {qr_code}")

        # We expect to find all 6 records (4 direct + 2 redis)
        success = (retrieved_count == 6)
        print_test_result("Data Retrieval", success,
                         f"Retrieved: {retrieved_count}/6 records")

        if success:
            config.test_results['passed'] += 1
        else:
            config.test_results['failed'] += 1

        return success

    except Exception as e:
        printBoldRed(f"Error in data retrieval: {e}")
        print_test_result("Data Retrieval", False, str(e))
        config.test_results['failed'] += 1
        import traceback
        traceback.print_exc()
        return False


def test_09_torque_values(config: TestConfig) -> bool:
    """Test 9: Torque Value Storage and Retrieval."""
    print_test_header("Torque Value Validation")

    db_name = getDatabaseName()

    try:
        # Check torque values for records we inserted
        test_qr = config.test_qr_codes[0]
        record = get_record_from_database(test_qr, db_name)

        if not record:
            printBoldRed(f"Could not retrieve record: {test_qr}")
            print_test_result("Torque Values", False, "Record not found")
            config.test_results['failed'] += 1
            return False

        torque = record['nut_tightening_torque_1']
        torque2 = record['nut_tightening_torque_2']
        free_rotation = record['free_rotation_torque_1']
        printBoldBlue(f"Torque value: {torque}, torque 2: {torque2}, free rotation torque 1: {free_rotation} "
                      f"({record['free_rotation_torque_1_result']})")

        # Verify floats and values exactly as inserted by test 02 (record 1: 45.5 / 50.0 / 5.5)
        if isinstance(torque, (int, float)):
            if (40.0 <= torque <= 60.0 and abs(torque - 45.5) < 1e-6 and abs(torque2 - 50.0) < 1e-6
                    and abs(free_rotation - 5.5) < 1e-6):
                printBoldGreen(f"✓ Torque value valid: {torque}")
                print_test_result("Torque Values", True, f"Torque: {torque}")
                config.test_results['passed'] += 1
                return True
            else:
                printBoldRed(f"✗ Torque out of range: {torque}")
                print_test_result("Torque Values", False, "Out of range")
                config.test_results['failed'] += 1
                return False
        else:
            printBoldRed(f"✗ Torque wrong type: {type(torque)}")
            print_test_result("Torque Values", False, "Wrong type")
            config.test_results['failed'] += 1
            return False

    except Exception as e:
        printBoldRed(f"Error testing torque: {e}")
        print_test_result("Torque Values", False, str(e))
        config.test_results['failed'] += 1
        import traceback
        traceback.print_exc()
        return False


def test_10_image_files(config: TestConfig) -> bool:
    """Test 10: Image files written by DBServer for the Redis records (9 inspection pictures each)."""
    print_test_header("Image File Creation and Storage")

    db_name = getDatabaseName()
    try:
        missing, found = [], 0
        for qr_code in config.test_qr_codes[4:6]:
            record = get_record_from_database(qr_code, db_name)
            if not record:
                missing.append(f"{qr_code}: record not found")
                continue
            for name, path in record['imagefiles'].items():
                if path and os.path.isfile(path) and cv2.imread(path) is not None:
                    found += 1
                else:
                    missing.append(f"{qr_code}/{name}: '{path}'")
        for m in missing:
            printBoldRed(f"✗ Missing image: {m}")
        passed = (found == 18 and not missing)
        print_test_result("Image Files", passed, f"{found}/18 image files present and readable "
                          f"(base folder: {os.path.abspath(CosThetaConfigurator.getInstance().getBaseFolderForImages())})")
        config.test_results['passed' if passed else 'failed'] += 1
        return passed
    except Exception as e:
        print_test_result("Image Files", False, str(e))
        config.test_results['failed'] += 1
        import traceback
        traceback.print_exc()
        return False


def test_11_cleanup(config: TestConfig) -> bool:
    """Test 11: Cleanup Test Data."""
    print_test_header("Cleanup Test Data")

    try:
        db_name = getDatabaseName()

        printBoldBlue("Cleaning up test records...")
        clean_test_data(config.test_qr_codes, db_name)

        # Verify cleanup
        remaining = 0
        for qr_code in config.test_qr_codes:
            if verify_record_in_database(qr_code, db_name):
                printBoldYellow(f"⚠ Still exists: {qr_code}")
                remaining += 1

        if remaining == 0:
            printBoldGreen(f"✓ All test records cleaned up")
            print_test_result("Cleanup", True, "All test data removed")
            config.test_results['passed'] += 1
            return True
        else:
            printBoldYellow(f"⚠ {remaining} records still in database")
            print_test_result("Cleanup", False, f"{remaining} records remain")
            config.test_results['failed'] += 1
            return False

    except Exception as e:
        printBoldRed(f"Error in cleanup: {e}")
        print_test_result("Cleanup", False, str(e))
        config.test_results['failed'] += 1
        import traceback
        traceback.print_exc()
        return False


# =============================================================================
# Main Test Runner
# =============================================================================

def run_all_tests() -> None:
    """Run all test cases."""
    config = TestConfig()

    printBoldBlue("=" * 80)
    printBoldBlue("DATABASE AND PERSISTENCE TEST SUITE")
    printBoldBlue("=" * 80)
    printBoldBlue(f"Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    printBoldBlue("=" * 80)

    # Run tests in sequence (EXCLUDING cleanup)
    tests = [
        test_01_database_connection,
        test_02_direct_insertions,
        test_03_duplicate_qr_handling,
        test_04_redis_connection,
        test_05_start_dbserver,
        test_06_redis_insertions,
        test_07_datetime_handling,
        test_08_data_retrieval,
        test_09_torque_values,
        test_10_image_files,
        # NOTE: test_11_cleanup runs AFTER DBServer is stopped
    ]

    for test_func in tests:
        try:
            test_func(config)
            print()  # Blank line between tests
        except KeyboardInterrupt:
            printBoldRed("\n\nTest interrupted by user")
            break
        except Exception as e:
            printBoldRed(f"\n\nUnexpected error in {test_func.__name__}: {e}")
            import traceback
            traceback.print_exc()

    # Print summary
    printBoldBlue("=" * 80)
    printBoldBlue("TEST SUMMARY")
    printBoldBlue("=" * 80)
    printBoldGreen(f"✓ PASSED: {config.test_results['passed']}")
    if config.test_results['failed'] > 0:
        printBoldRed(f"✗ FAILED: {config.test_results['failed']}")
    else:
        printBoldBlue(f"✗ FAILED: {config.test_results['failed']}")

    if config.test_results['skipped'] > 0:
        printBoldYellow(f"⊘ SKIPPED: {config.test_results['skipped']}")

    total = sum(config.test_results.values())
    if total > 0:
        pass_rate = (config.test_results['passed'] / total) * 100
        printBoldBlue(f"Pass Rate: {pass_rate:.1f}%")

    # CRITICAL: Stop DBServer BEFORE cleanup
    if config.db_server:
        try:
            config.db_server.stop()
            printBoldBlue("DBServer stopped")
            time.sleep(3)  # Give threads time to actually finish
        except:
            pass

    # Clear any remaining items in Redis
    try:
        if config.redis_connection is not None:
            clearQueues(config.redis_connection)
            printBoldBlue("Redis queues cleared")
    except:
        pass

    # NOW run cleanup after everything has stopped
    try:
        test_11_cleanup(config)
    except Exception as e:
        printBoldRed(f"Error in cleanup: {e}")

    printBoldBlue(f"End Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    printBoldBlue("=" * 80)
    return config.test_results['failed']

if __name__ == '__main__':
    try:
        failures = run_all_tests()
        # os._exit: DBServer's logger threads are non-daemon and would keep the process alive
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(1 if failures else 0)
    except KeyboardInterrupt:
        printBoldRed("\n\nTests interrupted by user")
        sys.exit(1)
    except Exception as e:
        printBoldRed(f"\n\nFatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)