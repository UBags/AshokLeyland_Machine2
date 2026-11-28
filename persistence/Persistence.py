from utils.SuppressedExceptions import noteSuppressed   # logs exceptions that are caught and ignored
import csv
import base64
from io import StringIO
from datetime import datetime, date
from typing import Union, List, Any, Optional

from contextlib import closing
import os
# Every PostgreSQL connection of this process gives up after 5 s if the server does not answer, instead of waiting
# for ever (libpq reads PGCONNECT_TIMEOUT; an explicit connect_timeout= still wins). Before, a PostgreSQL that hung
# held the caller for good: the QR code server's duplicate check, the IOServer's machine settings, the DB server.
os.environ.setdefault("PGCONNECT_TIMEOUT", "5")
import psycopg2
from psycopg2 import Error
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from dateutil.parser import parse

# From utils package
from utils.CosThetaFileUtils import createDirectory

# From BaseUtils
from BaseUtils import (
    getFullyQualifiedName
)

from utils.CosThetaPrintUtils import (printBoldRed,
                                      printBoldGreen,
                                      printBoldYellow,
                                      printBoldBlue,
                                      printLight
                                      )

# From Configuration
from Configuration import CosThetaConfigurator
from utils.RedisUtils import notok, ok

# NOTE: The following import appears to be UNUSED and can be removed:
# from utils.RedisUtils import *

CosThetaConfigurator.getInstance()  # needs to be called to ensure that configurations are properly loaded

reportMajorDatabaseActions = True
reportMinorDatabaseActions = True
# mode: str = CosThetaConfigurator.getInstance().getApplicationMode()

# To check if Persistence is getting loaded multiple times
# printBoldYellow(f"Initializing Persistence module: {id(__name__)}")

# *********************************Database and Schema Names******************************

# THREAD-SAFETY WARNING: These global variables are NOT thread-safe.
# For multi-threaded/web applications, consider using a class-based approach
# (e.g., PersistenceManager) to encapsulate state or pass configs as parameters.

currentMode: str = "Production"
database_name: str = "ashok_leyland_production"
data_subfolder: str = "Production/"
schema_name: str = "al_hub_and_disc_assembly_schema"


def getCurrentMode() -> str:
    return currentMode


def getDatabaseName() -> str:
    return database_name


def getDataSubFolder() -> str:
    return data_subfolder


def getSchemaName() -> str:
    return schema_name


# *********************************Folders For Images******************************

folderForKnuckleImages: str = ""
folderForHubAndBottomBearingImages: str = ""
folderForTopBearingImages: str = ""
folderForNutAndPlateWasherImages: str = ""
folderForBungAndNoBungImages: str = ""
folderForSplitPinAndWasherImages: str = ""
folderForCapImages: str = ""


def getFolderForKnuckleImages() -> str:
    return folderForKnuckleImages


def getFolderForHubAndBottomBearingImages() -> str:
    return folderForHubAndBottomBearingImages


def getFolderForTopBearingImages() -> str:
    return folderForTopBearingImages


def getFolderForNutAndPlateWasherImages() -> str:
    return folderForNutAndPlateWasherImages


def getFolderForBungAndNoBungImages() -> str:
    return folderForBungAndNoBungImages


def getFolderForSplitPinAndWasherImages() -> str:
    return folderForSplitPinAndWasherImages


def getFolderForCapImages() -> str:
    return folderForCapImages


allowed_roles = ["Administrator", "Supervisor", "Operator"]
logSource = getFullyQualifiedName(__file__)

# *********************************Database Statements******************************
create_database = "CREATE DATABASE {};"
query_get_database_names = '''SELECT datname,
    owner as Owner,
    encoding as Encoding,
    collate as Collate,
    ctype as Ctype,
    privileges as "Access Privileges"
    FROM pg_database;
    '''


def printAllVariables() -> None:
    printLight(f"{getCurrentMode() = }")
    printLight(f"{getDatabaseName() = }")
    printLight(f"{getDataSubFolder() = }")
    printLight(f"{getSchemaName() = }")
    printLight(f"{getFolderForKnuckleImages() = }")
    printLight(f"{getFolderForHubAndBottomBearingImages() = }")
    printLight(f"{getFolderForTopBearingImages() = }")
    printLight(f"{getFolderForNutAndPlateWasherImages() = }")
    printLight(f"{getFolderForBungAndNoBungImages() = }")
    printLight(f"{getFolderForSplitPinAndWasherImages() = }")
    printLight(f"{getFolderForCapImages() = }")


def setDatabaseName(mode: str, createDB: bool = True) -> str:
    """
    Set database name based on mode and optionally create/initialize database.

    THREAD-SAFETY WARNING: Modifies global variables. Not thread-safe.
    Consider using a class-based approach for multi-threaded applications.

    Args:
        mode: "test" or "production"
        createDB: Whether to create database and initialize tables

    Returns:
        str: Database name
    """
    global database_name, data_subfolder, currentMode
    global folderForKnuckleImages, folderForHubAndBottomBearingImages, folderForTopBearingImages, folderForNutAndPlateWasherImages
    global folderForBungAndNoBungImages, folderForSplitPinAndWasherImages, folderForCapImages
    if mode.lower() == "test":
        database_name = "ashok_leyland_test"
        data_subfolder = "Test/"
        currentMode = "Test"
    else:
        database_name = "ashok_leyland_production"
        data_subfolder = "Production/"
        currentMode = "Production"
    # printBoldBlue(f"In Persistence, {currentMode = }, {database_name = }, {data_subfolder = }")
    if createDB:
        createDatabase(db_name=getDatabaseName())
        createSchema(db_name=getDatabaseName())
        createAllTables(db_name=getDatabaseName())
        ensureIndexesExist(db_name=getDatabaseName(), verbose=reportMajorDatabaseActions)
        insertDefaultMachineSettings(db_name=getDatabaseName())
        insertRole(role_name=allowed_roles[0], db_name=getDatabaseName())
        insertRole(role_name=allowed_roles[1], db_name=getDatabaseName())
        insertRole(role_name=allowed_roles[2], db_name=getDatabaseName())
        insertNewUser(username="admin", password="admin123", first_name="Administrator", middle_name="", last_name="",
                      email="", role_name="Administrator", mobile="", db_name=getDatabaseName())
        insertNewUser(username="costheta", password="costheta123", first_name="Costheta", middle_name="",
                      last_name="User", email="", role_name="Administrator", mobile="", db_name=getDatabaseName())
        insertNewUser(getDatabaseName(), "superuser", "al_costheta@123", "Ashok_Leyland", "", "Superuser",
                      "Administrator", "", "",
                      remarks="")

    return database_name


def checkIfDatabaseExists(dbname: str) -> bool:
    exists = False
    aConnection = None
    try:
        aConnection = psycopg2.connect(user="postgres",
                                       password="postgres",
                                       host="127.0.0.1",
                                       port="5432")
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()
        aCursor.execute(f"SELECT datname FROM pg_database WHERE datname='{dbname}'")
        rows = aCursor.fetchall()
        exists = bool(rows)
        aConnection.commit()
        aCursor.close()
        aConnection.close()
    except psycopg2.OperationalError as error:
        printBoldRed(f"Connection error while checking if database exists: {error}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
    except Exception as error:
        if not isinstance(error, psycopg2.errors.UniqueViolation):
            printBoldRed(f"Error checking if database exists: {error}, {type(error)}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
        # raise error
    return exists


def createDatabase(db_name: str) -> None:
    aConnection = None
    try:
        exists = checkIfDatabaseExists(dbname=db_name)
    except Exception:
        exists = False
    if exists:
        return
    try:
        aConnection = psycopg2.connect(user="postgres",
                                       password="postgres",
                                       host="127.0.0.1",
                                       port="5432")
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()
        createDBStatement = sql.SQL(create_database).format(sql.Identifier(db_name))
        aCursor.execute(createDBStatement)
        aConnection.commit()
        aCursor.close()
        aConnection.close()
    except psycopg2.errors.DuplicateDatabase:
        # Database already exists, this is fine
        noteSuppressed()
    except psycopg2.OperationalError as error:
        printBoldRed(f"Connection error while creating database {db_name}: {error}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
    except Exception as error:
        if not isinstance(error, psycopg2.errors.UniqueViolation):
            printBoldRed(f"Error creating database {db_name}: {error}, {type(error)}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
        # raise error


def showAllDatabases() -> None:
    aConnection = None
    try:
        aConnection = psycopg2.connect(user="postgres",
                                       password="postgres",
                                       host="127.0.0.1",
                                       port="5432")
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()
        aCursor.execute("""SELECT datname from pg_database""")
        rows = aCursor.fetchall()
        # for row in rows:
        #     print("   ", row)
        aConnection.commit()
        aCursor.close()
        aConnection.close()
    except Exception as error:
        if not isinstance(error, psycopg2.errors.UniqueViolation):
            printBoldRed(f"Message : {error}, {type(error)} while showing all databases")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()


# *********************************Check Connection Statements******************************

enable_pgcrypto = "CREATE EXTENSION IF NOT EXISTS pgcrypto;"


def checkConnection(db_name: str) -> bool:
    aConnection = None
    try:
        aConnection = psycopg2.connect(user="postgres",
                                       password="postgres",
                                       host="127.0.0.1",
                                       port="5432",
                                       database=db_name)
        # # printBoldYellow(f"Established database connection with {db_name} in Persistence")
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()
        # aCursor.execute(enable_pgcrypto)
        aCursor.execute("SELECT version();")
        aCursor.fetchone()
        aConnection.commit()
        aCursor.close()
        aConnection.close()
        return True
    except Exception as error:
        printBoldRed(error)
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
        return False


# *********************************Schema Statements******************************

create_schema = "CREATE SCHEMA IF NOT EXISTS {};".format(schema_name)
query_get_schema_names = '''SELECT * FROM pg_catalog.pg_namespace ORDER BY nspname;'''


def createSchema(db_name: str) -> None:
    aConnection = None
    try:
        aConnection = psycopg2.connect(user="postgres",
                                       password="postgres",
                                       host="127.0.0.1",
                                       port="5432",
                                       database=db_name)
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()
        createSchemaStatement = sql.SQL(create_schema)
        aCursor.execute(createSchemaStatement)
        aConnection.commit()
        aCursor.close()
        aConnection.close()
    except psycopg2.errors.DuplicateSchema:
        # Schema already exists, this is fine
        noteSuppressed()
    except psycopg2.OperationalError as error:
        printBoldRed(f"Connection error while creating schema {schema_name}: {error}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
    except Exception as error:
        if not isinstance(error, psycopg2.errors.UniqueViolation):
            printBoldRed(f"Error creating schema {schema_name}: {error}, {type(error)}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
        # raise error


# *********************************Folders for storing images******************************
def getTodaysDateAsAFolder() -> str:
    return f'{datetime.now().strftime("%Y-%m-%d")}/'


def makeFoldersForImages(mode_data_subfolder):
    global folderForKnuckleImages, folderForHubAndBottomBearingImages, folderForTopBearingImages, folderForNutAndPlateWasherImages
    global folderForBungAndNoBungImages, folderForSplitPinAndWasherImages, folderForCapImages
    folderForKnuckleImages = f"{CosThetaConfigurator.getInstance().getBaseFolderForImages()}{getDataSubFolder()}{CosThetaConfigurator.getInstance().getFolderForKnuckleImages()}{getTodaysDateAsAFolder()}"
    folderForHubAndBottomBearingImages = f"{CosThetaConfigurator.getInstance().getBaseFolderForImages()}{getDataSubFolder()}{CosThetaConfigurator.getInstance().getFolderForHubAndBottomBearingImages()}{getTodaysDateAsAFolder()}"
    folderForTopBearingImages = f"{CosThetaConfigurator.getInstance().getBaseFolderForImages()}{getDataSubFolder()}{CosThetaConfigurator.getInstance().getFolderForTopBearingImages()}{getTodaysDateAsAFolder()}"
    folderForNutAndPlateWasherImages = f"{CosThetaConfigurator.getInstance().getBaseFolderForImages()}{getDataSubFolder()}{CosThetaConfigurator.getInstance().getFolderForNutAndPlateWasherImages()}{getTodaysDateAsAFolder()}"
    folderForBungAndNoBungImages = f"{CosThetaConfigurator.getInstance().getBaseFolderForImages()}{getDataSubFolder()}{CosThetaConfigurator.getInstance().getFolderForBungAndNoBungImages()}{getTodaysDateAsAFolder()}"
    folderForSplitPinAndWasherImages = f"{CosThetaConfigurator.getInstance().getBaseFolderForImages()}{getDataSubFolder()}{CosThetaConfigurator.getInstance().getFolderForSplitPinAndWasherImages()}{getTodaysDateAsAFolder()}"
    folderForCapImages = f"{CosThetaConfigurator.getInstance().getBaseFolderForImages()}{getDataSubFolder()}{CosThetaConfigurator.getInstance().getFolderForCapImages()}{getTodaysDateAsAFolder()}"
    # print(f"{folderForKnuckleImages = }")
    createDirectory(folderForKnuckleImages)
    createDirectory(folderForHubAndBottomBearingImages)
    createDirectory(folderForTopBearingImages)
    createDirectory(folderForNutAndPlateWasherImages)
    createDirectory(folderForBungAndNoBungImages)
    createDirectory(folderForSplitPinAndWasherImages)
    createDirectory(folderForCapImages)


# *********************************Some constants******************************

okResult = CosThetaConfigurator.getInstance().getOkCommand()
notokResult = CosThetaConfigurator.getInstance().getNotOKCommand()
notChecked: str = "Not Checked"
bypassedResult: str = "Bypassed"            # step skipped by the Machine Setting (frontend BYPASSED_TEXT)
notPopulated: str = "Not Populated"
defaultTorqueValue: float = -0.1

# *********************************Table creation statements******************************

create_tables_string: str = '''
-- Create the sequence
CREATE SEQUENCE IF NOT EXISTS al_hub_and_disc_assembly_schema.roles_role_id_seq
    INCREMENT 1
    START 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1;

-- Create the table
CREATE TABLE IF NOT EXISTS al_hub_and_disc_assembly_schema.roles
(
    role_id integer NOT NULL DEFAULT nextval('al_hub_and_disc_assembly_schema.roles_role_id_seq'::regclass),
    role_name character varying(255) COLLATE pg_catalog."default" NOT NULL,
    modules_access character varying(255) COLLATE pg_catalog."default",
    CONSTRAINT roles_pkey PRIMARY KEY (role_id),
	CONSTRAINT roles_rolename_key UNIQUE (role_name)
);

-- Create the sequence
CREATE SEQUENCE IF NOT EXISTS al_hub_and_disc_assembly_schema.users_user_id_seq
    INCREMENT BY 1
    START WITH 100
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1;

-- Create the table
CREATE TABLE IF NOT EXISTS al_hub_and_disc_assembly_schema.users
(
    user_id integer NOT NULL DEFAULT nextval('al_hub_and_disc_assembly_schema.users_user_id_seq'::regclass),
    username character varying(100) COLLATE pg_catalog."default" NOT NULL,
    password character varying(1024) COLLATE pg_catalog."default" NOT NULL,
    first_name character varying(100) COLLATE pg_catalog."default" NOT NULL,
    middle_name character varying(100) COLLATE pg_catalog."default",
    last_name character varying(100) COLLATE pg_catalog."default",
    role_name character varying(255) COLLATE pg_catalog."default" NOT NULL,
    email character varying(255) COLLATE pg_catalog."default",
    mobile character varying(255) COLLATE pg_catalog."default",
    active_status character varying(20) COLLATE pg_catalog."default" NOT NULL DEFAULT 'YES'::character varying,
    remarks character varying(255) COLLATE pg_catalog."default",
    created_on timestamp without time zone NOT NULL,
    last_login timestamp without time zone,
    CONSTRAINT users_pkey PRIMARY KEY (user_id),
    CONSTRAINT users_username_key UNIQUE (username),
    CONSTRAINT users_role_name_fkey FOREIGN KEY (role_name)
        REFERENCES al_hub_and_disc_assembly_schema.roles (role_name) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE NO ACTION
);

-- Create the sequence
CREATE SEQUENCE IF NOT EXISTS al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data_record_id_seq
    INCREMENT BY 1
    START WITH 1000
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1;

-- Create the table
CREATE TABLE IF NOT EXISTS al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data
(
    record_id integer NOT NULL DEFAULT nextval('al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data_record_id_seq'::regclass),
    qr_code character varying(255) COLLATE pg_catalog."default" NOT NULL,
    model_name character varying(255) COLLATE pg_catalog."default" DEFAULT 'Not Populated',
    lhs_rhs character varying(255) COLLATE pg_catalog."default" DEFAULT 'Not Populated',
    model_tonnage character varying(255) COLLATE pg_catalog."default" DEFAULT 'Not Populated',
    component_assembly_start_datetime timestamp without time zone,
    check_knuckle_imagefile character varying(255) COLLATE pg_catalog."default" DEFAULT 'Not Populated',
    check_knuckle_result character varying(15) COLLATE pg_catalog."default" NOT NULL DEFAULT 'Not Checked',
    check_knuckle_datetime timestamp without time zone,
    check_hub_and_bottom_bearing_imagefile character varying(255) COLLATE pg_catalog."default" DEFAULT 'Not Populated',
    check_hub_and_bottom_bearing_result character varying(15) COLLATE pg_catalog."default" NOT NULL DEFAULT 'Not Checked',
    check_hub_and_bottom_bearing_datetime timestamp without time zone,
    check_top_bearing_imagefile character varying(255) COLLATE pg_catalog."default" DEFAULT 'Not Populated',
    check_top_bearing_result character varying(15) COLLATE pg_catalog."default" NOT NULL DEFAULT 'Not Checked',
    check_top_bearing_datetime timestamp without time zone,
    check_nut_and_platewasher_imagefile character varying(255) COLLATE pg_catalog."default" DEFAULT 'Not Populated',
    check_nut_and_platewasher_result character varying(15) COLLATE pg_catalog."default" NOT NULL DEFAULT 'Not Checked',
    check_nut_and_platewasher_datetime timestamp without time zone,
    nut_tightening_torque_1 float DEFAULT -1.0 NOT NULL,
    nut_tightening_torque_1_result character varying(15) COLLATE pg_catalog."default" NOT NULL DEFAULT 'Not Checked',
    nut_tightening_torque_1_datetime timestamp without time zone,
    check_bung_for_component_press_imagefile character varying(255) COLLATE pg_catalog."default" DEFAULT 'Not Populated',
    check_bung_for_component_press_result character varying(15) COLLATE pg_catalog."default" NOT NULL DEFAULT 'Not Checked',
    check_bung_for_component_press_datetime timestamp without time zone,
    component_press_done character varying(15) COLLATE pg_catalog."default" NOT NULL DEFAULT 'Not Checked',
    component_press_datetime timestamp without time zone,
    check_no_bung_imagefile character varying(255) COLLATE pg_catalog."default" DEFAULT 'Not Populated',
    check_no_bung_result character varying(15) COLLATE pg_catalog."default" NOT NULL DEFAULT 'Not Checked',
    check_no_bung_datetime timestamp without time zone,
    nut_tightening_torque_2 float DEFAULT -1.0 NOT NULL,
    nut_tightening_torque_2_result character varying(15) COLLATE pg_catalog."default" NOT NULL DEFAULT 'Not Checked',
    nut_tightening_torque_2_datetime timestamp without time zone,
    check_splitpin_and_washer_imagefile character varying(255) COLLATE pg_catalog."default" DEFAULT 'Not Populated',
    check_splitpin_and_washer_result character varying(15) COLLATE pg_catalog."default" NOT NULL DEFAULT 'Not Checked',
    check_splitpin_and_washer_datetime timestamp without time zone,
    check_cap_imagefile character varying(255) COLLATE pg_catalog."default" DEFAULT 'Not Populated',
    check_cap_result character varying(15) COLLATE pg_catalog."default" NOT NULL DEFAULT 'Not Checked',
    check_cap_datetime timestamp without time zone,
    check_bung_cap_press_imagefile character varying(255) COLLATE pg_catalog."default" DEFAULT 'Not Populated',
    check_bung_cap_press_result character varying(15) COLLATE pg_catalog."default" NOT NULL DEFAULT 'Not Checked',
    check_bung_cap_press_datetime timestamp without time zone,
    cap_press_done character varying(15) COLLATE pg_catalog."default" NOT NULL DEFAULT 'Not Checked',
    cap_press_datetime timestamp without time zone,
    free_rotation_torque_1 float DEFAULT -1.0 NOT NULL,
    free_rotation_torque_1_result character varying(15) COLLATE pg_catalog."default" NOT NULL DEFAULT 'Not Checked',
    free_rotation_torque_1_datetime timestamp without time zone,
    -- Machine settings written to the PLC in state 2 for this component (traceability; NULL if not written)
    setting_rotation_rpm real,
    setting_tightening_torque1_setpoint real,
    setting_tightening_torque2_setpoint real,
    setting_no_of_rotation1_ccw real,
    setting_no_of_rotation1_cw real,
    -- the order of rotations of the component's side (CW.CCW / CCW.CW) in machine_settings (CHANGES 234); NULL if not known
    setting_rotation_order character varying(10) COLLATE pg_catalog."default",
    -- PLC_PC_Station2RotationDuringTighteningDone read with each Station 2 torque (NULL if the PLC does not provide it)
    nut_tightening_torque_1_rotation_done boolean,
    nut_tightening_torque_2_rotation_done boolean,
    -- the cycle was aborted (emergency button on the machine, or manual abort in the frontend), and why
    aborted boolean NOT NULL DEFAULT false,
    abort_reason character varying(255) COLLATE pg_catalog."default",
    -- the bung, component press, bung removal and second tightening were bypassed (the model's Machine Setting); those
    -- steps then have result 'Bypassed', no date, torque 2 = -1.0
    bypassed_press_and_tightening2 boolean NOT NULL DEFAULT false,
    ok_notok_result character varying(15) COLLATE pg_catalog."default" NOT NULL DEFAULT 'Not OK',
    username character varying(100) COLLATE pg_catalog."default" NOT NULL DEFAULT 'Default User',
    remarks character varying(255) COLLATE pg_catalog."default",
    created_on timestamp without time zone NOT NULL,
    CONSTRAINT hub_and_disc_assembly_data_pkey PRIMARY KEY (record_id),
    CONSTRAINT hub_and_disc_assembly_data_username_fkey FOREIGN KEY (username)
        REFERENCES al_hub_and_disc_assembly_schema.users (username) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE NO ACTION
);

CREATE INDEX IF NOT EXISTS idx_model_name ON al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data(model_name);

CREATE INDEX IF NOT EXISTS idx_qr_code ON al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data (qr_code);

CREATE INDEX IF NOT EXISTS idx_created_on ON al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data(created_on);

-- 2026-11-17 (CHANGES 125): 'bunk' became 'bung' in the column names. Databases made before keep their data: each old
-- column is renamed (only if it exists and the new one does not; a no-op on new databases and on later starts).
DO $$
DECLARE
    pair text[];
BEGIN
    FOREACH pair SLICE 1 IN ARRAY ARRAY[
        ARRAY['check_bunk_for_component_press_imagefile', 'check_bung_for_component_press_imagefile'],
        ARRAY['check_bunk_for_component_press_result', 'check_bung_for_component_press_result'],
        ARRAY['check_bunk_for_component_press_datetime', 'check_bung_for_component_press_datetime'],
        ARRAY['check_no_bunk_imagefile', 'check_no_bung_imagefile'],
        ARRAY['check_no_bunk_result', 'check_no_bung_result'],
        ARRAY['check_no_bunk_datetime', 'check_no_bung_datetime'],
        ARRAY['check_bunk_cap_press_imagefile', 'check_bung_cap_press_imagefile'],
        ARRAY['check_bunk_cap_press_result', 'check_bung_cap_press_result'],
        ARRAY['check_bunk_cap_press_datetime', 'check_bung_cap_press_datetime']
    ] LOOP
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'al_hub_and_disc_assembly_schema'
                   AND table_name = 'hub_and_disc_assembly_data' AND column_name = pair[1])
           AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'al_hub_and_disc_assembly_schema'
                   AND table_name = 'hub_and_disc_assembly_data' AND column_name = pair[2]) THEN
            EXECUTE format('ALTER TABLE al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data RENAME COLUMN %I TO %I',
                           pair[1], pair[2]);
        END IF;
    END LOOP;
END $$;

-- Machine 2: the separate free-rotation step (M1 state 13) no longer exists.
-- Drop its columns from databases created by earlier versions (no-op on new databases).
ALTER TABLE al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data DROP COLUMN IF EXISTS free_rotations_done;
ALTER TABLE al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data DROP COLUMN IF EXISTS free_rotations_datetime;
ALTER TABLE al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data ADD COLUMN IF NOT EXISTS setting_rotation_rpm real;
ALTER TABLE al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data ADD COLUMN IF NOT EXISTS setting_tightening_torque1_setpoint real;
ALTER TABLE al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data ADD COLUMN IF NOT EXISTS setting_tightening_torque2_setpoint real;
ALTER TABLE al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data ADD COLUMN IF NOT EXISTS setting_no_of_rotation1_ccw real;
ALTER TABLE al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data ADD COLUMN IF NOT EXISTS setting_no_of_rotation1_cw real;
ALTER TABLE al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data ADD COLUMN IF NOT EXISTS setting_rotation_order character varying(10);
ALTER TABLE al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data ADD COLUMN IF NOT EXISTS nut_tightening_torque_1_rotation_done boolean;
ALTER TABLE al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data ADD COLUMN IF NOT EXISTS nut_tightening_torque_2_rotation_done boolean;
ALTER TABLE al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data ADD COLUMN IF NOT EXISTS aborted boolean NOT NULL DEFAULT false;
ALTER TABLE al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data ADD COLUMN IF NOT EXISTS abort_reason character varying(255);
ALTER TABLE al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data ADD COLUMN IF NOT EXISTS bypassed_press_and_tightening2 boolean NOT NULL DEFAULT false;

-- Machine 2 settings, edited in the frontend (Machine Settings menu, Administrator / Supervisor) and written by the
-- IOServer to the PLC in state 2 of every cycle. Append-only: every save adds a row; the latest row is in force.
-- Limits (also checked by the frontend): rotation RPM 20-40, tightening torque setpoints > 0 and < 20 Nm,
-- rotation counts of rounds 1 and 2: 1 to 4. All REAL, as the PLC takes them. Per model (model_name; the models of
-- qrcode.partmapping.N): the latest row of a model is in force for it. Rows without a model are from before
-- 2026-11-08 (one set for all models); they are copied to every model that has none. The order of the two rounds per
-- side (rotation_order_lhs / _rhs, CW.CCW or CCW.CW) says which round goes to the CW tag and which to the CCW tag.
-- A machine_settings table from Machine 1 (rotation counts 1/2 CW/CCW, RotationUnitRPM) is replaced.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'al_hub_and_disc_assembly_schema'
               AND table_name = 'machine_settings' AND column_name = 'rotationunitrpm') THEN
        DROP TABLE al_hub_and_disc_assembly_schema.machine_settings;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS al_hub_and_disc_assembly_schema.machine_settings
(
    setting_id integer NOT NULL GENERATED ALWAYS AS IDENTITY,
    rotation_rpm real NOT NULL CHECK (rotation_rpm BETWEEN 20 AND 40),
    tightening_torque1_setpoint real NOT NULL CHECK (tightening_torque1_setpoint > 0 AND tightening_torque1_setpoint < 20),
    tightening_torque2_setpoint real NOT NULL CHECK (tightening_torque2_setpoint > 0 AND tightening_torque2_setpoint < 20),
    -- rows from before 2026-11-28 (CHANGES 234) only; since then NULL (see the rounds and orders below)
    no_of_rotation1_ccw real CHECK (no_of_rotation1_ccw BETWEEN 1 AND 4),
    no_of_rotation1_cw real CHECK (no_of_rotation1_cw BETWEEN 1 AND 4),
    -- 2026-11-28 (CHANGES 234): rotations of the 1st and 2nd round, and the order of the rounds per side:
    -- CW.CCW = 1st round clockwise, 2nd round counter-clockwise; CCW.CW = the other way round
    no_of_rotations_round1 real NOT NULL CHECK (no_of_rotations_round1 BETWEEN 1 AND 4),
    no_of_rotations_round2 real NOT NULL CHECK (no_of_rotations_round2 BETWEEN 1 AND 4),
    rotation_order_lhs character varying(10) COLLATE pg_catalog."default" NOT NULL CHECK (rotation_order_lhs IN ('CW.CCW', 'CCW.CW')),
    rotation_order_rhs character varying(10) COLLATE pg_catalog."default" NOT NULL CHECK (rotation_order_rhs IN ('CW.CCW', 'CCW.CW')),
    model_name character varying(50) COLLATE pg_catalog."default",
    bypass_press_and_tightening2 boolean NOT NULL DEFAULT false,
    updated_by character varying(100) COLLATE pg_catalog."default" NOT NULL DEFAULT 'system',
    updated_on_date timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT machine_settings_pkey PRIMARY KEY (setting_id)
);

-- 2026-11-08: settings per model; rotation counts 1 to 4 (were 1 or 2). For databases made before.
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings ADD COLUMN IF NOT EXISTS model_name character varying(50);
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings DROP CONSTRAINT IF EXISTS machine_settings_no_of_rotation1_ccw_check;
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings DROP CONSTRAINT IF EXISTS machine_settings_no_of_rotation1_cw_check;
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings ADD CONSTRAINT machine_settings_no_of_rotation1_ccw_check CHECK (no_of_rotation1_ccw BETWEEN 1 AND 4);
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings ADD CONSTRAINT machine_settings_no_of_rotation1_cw_check CHECK (no_of_rotation1_cw BETWEEN 1 AND 4);
CREATE INDEX IF NOT EXISTS idx_machine_settings_model ON al_hub_and_disc_assembly_schema.machine_settings (model_name, setting_id);
-- 2026-11-13: per model, skip the bung, component press, bung removal and second tightening (PC_PLC_Skip_Second_Tightening)
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings ADD COLUMN IF NOT EXISTS bypass_press_and_tightening2 boolean NOT NULL DEFAULT false;
-- 2026-11-28 (CHANGES 234): rounds 1 / 2 and the order per side replace the CW / CCW counts. Rows from before get
-- round 1 = the CW count, round 2 = the CCW count, both orders CW.CCW: the PLC is given the same CW / CCW values as
-- before. The old columns stay (history), no longer written.
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings ADD COLUMN IF NOT EXISTS no_of_rotations_round1 real;
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings ADD COLUMN IF NOT EXISTS no_of_rotations_round2 real;
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings ADD COLUMN IF NOT EXISTS rotation_order_lhs character varying(10);
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings ADD COLUMN IF NOT EXISTS rotation_order_rhs character varying(10);
UPDATE al_hub_and_disc_assembly_schema.machine_settings
   SET no_of_rotations_round1 = no_of_rotation1_cw, no_of_rotations_round2 = no_of_rotation1_ccw
 WHERE no_of_rotations_round1 IS NULL OR no_of_rotations_round2 IS NULL;
UPDATE al_hub_and_disc_assembly_schema.machine_settings SET rotation_order_lhs = 'CW.CCW' WHERE rotation_order_lhs IS NULL;
UPDATE al_hub_and_disc_assembly_schema.machine_settings SET rotation_order_rhs = 'CW.CCW' WHERE rotation_order_rhs IS NULL;
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings ALTER COLUMN no_of_rotation1_ccw DROP NOT NULL;
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings ALTER COLUMN no_of_rotation1_cw DROP NOT NULL;
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings ALTER COLUMN no_of_rotations_round1 SET NOT NULL;
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings ALTER COLUMN no_of_rotations_round2 SET NOT NULL;
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings ALTER COLUMN rotation_order_lhs SET NOT NULL;
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings ALTER COLUMN rotation_order_rhs SET NOT NULL;
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings DROP CONSTRAINT IF EXISTS machine_settings_no_of_rotations_round1_check;
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings DROP CONSTRAINT IF EXISTS machine_settings_no_of_rotations_round2_check;
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings DROP CONSTRAINT IF EXISTS machine_settings_rotation_order_lhs_check;
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings DROP CONSTRAINT IF EXISTS machine_settings_rotation_order_rhs_check;
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings ADD CONSTRAINT machine_settings_no_of_rotations_round1_check CHECK (no_of_rotations_round1 BETWEEN 1 AND 4);
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings ADD CONSTRAINT machine_settings_no_of_rotations_round2_check CHECK (no_of_rotations_round2 BETWEEN 1 AND 4);
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings ADD CONSTRAINT machine_settings_rotation_order_lhs_check CHECK (rotation_order_lhs IN ('CW.CCW', 'CCW.CW'));
ALTER TABLE al_hub_and_disc_assembly_schema.machine_settings ADD CONSTRAINT machine_settings_rotation_order_rhs_check CHECK (rotation_order_rhs IN ('CW.CCW', 'CCW.CW'));
'''


def createAllTables(db_name: str) -> None:
    aConnection = None
    try:
        aConnection = psycopg2.connect(user="postgres",
                                       password="postgres",
                                       host="127.0.0.1",
                                       port="5432",
                                       database=db_name)
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()
        createAllTablesStatement = sql.SQL(create_tables_string)
        aCursor.execute(createAllTablesStatement)
        aConnection.commit()
        aCursor.close()
        aConnection.close()
    except psycopg2.OperationalError as error:
        printBoldRed(f"Connection error while creating tables in schema {schema_name}: {error}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
    except Exception as error:
        if not isinstance(error, psycopg2.errors.UniqueViolation):
            printBoldRed(f"Error creating tables in schema {schema_name}: {error}, {type(error)}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()


# *********************************Constants defining user actions******************************

CHANGED_OWN_PASSWORD = "Changed_Own_Password"
ADDED_NEW_USER = "Added_New_User"
CHANGED_OTHERS_PASSWORD = "Changed_Others_Password"
CHANGED_USER_ROLE = "Changed_User_Role"
INACTIVATED_USER = "Inactivated_User"
ACTIVATED_USER = "Activated_User"
SENT_FROM_TO_REPORT = "Sent_From_To_Report"
SENT_AUDIT_REPORT = "Sent_Audit_Report"
SAVED_FROM_TO_REPORT = "Saved_From_To_Report"
SAVED_AUDIT_REPORT = "Saved_Audit_Report"
LOGGED_IN = "Logged_In"
LOGGED_OUT = "Logged_Out"

# ************************* BEGIN - ROLES TABLE ******************************************

insert_table_roles = '''INSERT INTO {}.roles
    (role_id, role_name, modules_access)
    VALUES (
        DEFAULT,
        '{}',
        '{}'
    );
    '''


def insertRole(db_name: str, role_name: str, module_access="") -> bool:
    global schema_name
    done = False
    aConnection = None
    insertStatement = ''
    try:
        aConnection = psycopg2.connect(user="postgres",
                                       password="postgres",
                                       host="127.0.0.1",
                                       port="5432",
                                       database=db_name)
        # printBoldYellow(f"Established database connection with {db_name} in insertRole()")
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()
        # Use INSERT ... ON CONFLICT for idempotency and parameterized query
        insertStatement = sql.SQL("""
            INSERT INTO {schema}.roles (role_id, role_name, modules_access)
            VALUES (DEFAULT, %s, %s)
            ON CONFLICT (role_name) DO NOTHING
        """).format(schema=sql.Identifier(schema_name))

        aCursor.execute(insertStatement, (role_name, module_access))
        aConnection.commit()
        aCursor.close()
        aConnection.close()
        done = True
    except psycopg2.OperationalError as error:
        printBoldRed(f"Connection error while inserting role {role_name}: {error}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
    except Exception as error:
        if not isinstance(error, psycopg2.errors.UniqueViolation):
            printBoldRed(f"Error inserting role {role_name}: {error}, {type(error)}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
        # raise error
    return done


# ************************* END - ROLES TABLE ******************************************

def checkIfRecordExists(selectStatement: str, db_name: str) -> tuple[bool, int]:
    aConnection = None
    resultRecords = []
    fetchStatement = ''
    try:
        aConnection = psycopg2.connect(user="postgres",
                                       password="postgres",
                                       host="127.0.0.1",
                                       port="5432",
                                       database=db_name)
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()
        fetchStatement = sql.SQL(selectStatement)
        aCursor.execute(fetchStatement)
        resultRecords = aCursor.fetchall()
        aConnection.commit()
        aCursor.close()
        aConnection.close()
        nRecords = len(resultRecords)
        return True, nRecords
    except Exception as error:
        if not isinstance(error, psycopg2.errors.UniqueViolation):
            printBoldRed(f"Encountered {error}, {type(error)} for statement {fetchStatement}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
    return False, 0


# ************************* BEGIN - USERS TABLE ******************************************

insert_table_users = '''INSERT INTO
    {}.users (user_id, username, password, first_name, middle_name, last_name, role_name, email, mobile, active_status, remarks, created_on, last_login)
    VALUES (
        DEFAULT,
        '{}',
        '{}',
        '{}',
        '{}',
        '{}',
        '{}',
        '{}',
        '{}',
        DEFAULT,
        '{}',
        to_timestamp('{}', 'dd-mm-yyyy hh24:mi:ss'),
        to_timestamp('{}', 'dd-mm-yyyy hh24:mi:ss')
    );
    '''

query_table_users_for_specific_username_password_and_role = '''SELECT user_id, username, password, role_name
        FROM {}.users
        WHERE username = '{}';
        '''

query_table_users_for_active_users_passwords_roles = '''SELECT user_id, username, password, role_name
        FROM {}.users
        WHERE active_status = 'YES';
        '''

query_table_users_for_inactive_users_passwords = '''SELECT user_id, username, password, role_name
        FROM {}.users
        WHERE active_status = 'NO';
        '''

query_table_users_for_all_users_passwords_roles = '''SELECT user_id, username, password, role_name
        FROM {}.users;
        '''

update_table_users_with_password_for_specific_username = '''UPDATE {}.users
    SET password = '{}'
    WHERE username = '{}';
    '''

update_table_users_with_rolename_for_specific_username = '''UPDATE {}.users
    SET role_name = '{}'
    WHERE username = '{}';
    '''

inactivate_username_in_table_users = '''UPDATE {}.users
    SET active_status = 'NO'
    WHERE username = '{}';
    '''

activate_username_in_table_users = '''UPDATE {}.users
    SET active_status = 'YES'
    WHERE username = '{}';
    '''


# returns if the insert was executed, and the number of existing records it found with the username
def insertNewUser(db_name: str, username: str, password: str, first_name: str, middle_name: str,
                  last_name: str, role_name: str, email: str, mobile: str, remarks: str = ""):
    global schema_name
    checkUserExistsStatement = query_table_users_for_specific_username_password_and_role.format(schema_name, username)
    executed, nUsers = checkIfRecordExists(db_name=db_name, selectStatement=checkUserExistsStatement)
    # False == not executed; 0 == no existing record found
    if not executed:
        return False, 0
    # True == executed; 1 == at least one record found
    if nUsers > 0:
        return False, 1
    aConnection = None
    insertStatement = ''
    try:
        aConnection = psycopg2.connect(user="postgres",
                                       password="postgres",
                                       host="127.0.0.1",
                                       port="5432",
                                       database=db_name)
        # printBoldYellow(f"Established database connection with {db_name} in Persistence")
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()
        # Use parameterized query to prevent SQL injection
        insertStatement = sql.SQL("""
            INSERT INTO {schema}.users 
                (user_id, username, password, first_name, middle_name, last_name, role_name, 
                 email, mobile, active_status, remarks, created_on, last_login)
            VALUES (DEFAULT, %s, %s, %s, %s, %s, %s, %s, %s, DEFAULT, %s, 
                    to_timestamp(%s, 'DD-MM-YYYY HH24:MI:SS'), 
                    to_timestamp(%s, 'DD-MM-YYYY HH24:MI:SS'))
        """).format(schema=sql.Identifier(schema_name))

        current_time = datetime.now().strftime("%d-%m-%Y %H:%M:%S")

        aCursor.execute(insertStatement, (
            username, encodeString(password), first_name, middle_name,
            last_name, role_name, email, mobile, remarks,
            current_time, current_time
        ))
        aConnection.commit()
        aCursor.close()
        aConnection.close()
    except psycopg2.OperationalError as error:
        printBoldRed(f"Connection error while inserting role {role_name}: {error}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
    except Exception as error:
        if not isinstance(error, psycopg2.errors.UniqueViolation):
            printBoldRed(f"Error inserting role {role_name}: {error}, {type(error)}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
        # False == not executed; 0 == no existing record found
        return False, 0
    # True == executed; 0 == no record found
    return True, 0


def getIdAndUsernamesAndPasswordsAndRolesForActiveUsers(db_name: str):
    global schema_name
    aConnection = None
    fetchStatement = ''
    resultRecords = []
    try:
        aConnection = psycopg2.connect(user="postgres",
                                       password="postgres",
                                       host="127.0.0.1",
                                       port="5432",
                                       database=db_name)
        # printBoldYellow(f"Established database connection with {db_name} in Persistence")
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()
        fetchStatement = query_table_users_for_active_users_passwords_roles.format(schema_name)
        fetchStatement = sql.SQL(fetchStatement)
        aCursor.execute(fetchStatement)
        interimResultRecords = aCursor.fetchall()
        aConnection.commit()
        aCursor.close()
        aConnection.close()
        for aRecord in interimResultRecords:
            recordAsList = []
            for i in range(len(aRecord)):
                if i != 2:
                    recordAsList.append(aRecord[i])
                else:
                    recordAsList.append(decodeString(aRecord[i]))
            resultRecords.append(recordAsList)
    except Exception as error:
        if not isinstance(error, psycopg2.errors.UniqueViolation):
            printBoldRed(f"Encountered {error}, {type(error)} for statement {fetchStatement}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
    return resultRecords


def getIdAndUsernamesAndPasswordsForInactiveUsers(db_name: str) -> List[List]:
    global schema_name
    aConnection = None
    fetchStatement = ''
    resultRecords = []
    try:
        aConnection = psycopg2.connect(user="postgres",
                                       password="postgres",
                                       host="127.0.0.1",
                                       port="5432",
                                       database=db_name)
        # printBoldYellow(f"Established database connection with {db_name} in Persistence")
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()
        fetchStatement = query_table_users_for_inactive_users_passwords.format(schema_name)
        fetchStatement = sql.SQL(fetchStatement)
        aCursor.execute(fetchStatement)
        interimResultRecords = aCursor.fetchall()
        aConnection.commit()
        aCursor.close()
        aConnection.close()
        for aRecord in interimResultRecords:
            recordAsList = []
            for i in range(len(aRecord)):
                if i != 2:
                    recordAsList.append(aRecord[i])
                else:
                    recordAsList.append(decodeString(aRecord[i]))
            resultRecords.append(recordAsList)
    except Exception as error:
        if not isinstance(error, psycopg2.errors.UniqueViolation):
            printBoldRed(f"Encountered {error}, {type(error)} for statement {fetchStatement}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
    return resultRecords


def getIdAndUsernamesAndPasswordsForAllUsers(db_name: str) -> List[List]:
    global schema_name
    aConnection = None
    fetchStatement = ''
    resultRecords = []
    try:
        aConnection = psycopg2.connect(user="postgres",
                                       password="postgres",
                                       host="127.0.0.1",
                                       port="5432",
                                       database=db_name)
        # printBoldYellow(f"Established database connection with {db_name} in Persistence")
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()
        fetchStatement = query_table_users_for_all_users_passwords_roles.format(schema_name)
        fetchStatement = sql.SQL(fetchStatement)
        aCursor.execute(fetchStatement)
        interimResultRecords = aCursor.fetchall()
        aConnection.commit()
        aCursor.close()
        aConnection.close()
        for aRecord in interimResultRecords:
            recordAsList = []
            for i in range(len(aRecord)):
                if i != 2:
                    recordAsList.append(aRecord[i])
                else:
                    recordAsList.append(decodeString(aRecord[i]))
            resultRecords.append(recordAsList)
    except Exception as error:
        if not isinstance(error, psycopg2.errors.UniqueViolation):
            printBoldRed(f"Encountered {error}, {type(error)} for statement {fetchStatement}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
    return resultRecords


def updatePassword(username: str, password: str, db_name: str) -> bool:
    global schema_name
    aConnection = None
    updateStatement = ''
    try:
        aConnection = psycopg2.connect(user="postgres",
                                       password="postgres",
                                       host="127.0.0.1",
                                       port="5432",
                                       database=db_name)
        # printBoldYellow(f"Established database connection with {db_name} in Persistence")
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()
        # Use parameterized query to prevent SQL injection
        updateStatement = sql.SQL("UPDATE {schema}.users SET password = %s WHERE username = %s").format(
            schema=sql.Identifier(schema_name)
        )
        aCursor.execute(updateStatement, (encodeString(password), username))
        aConnection.commit()
        aCursor.close()
        aConnection.close()
    except Exception as error:
        if not isinstance(error, psycopg2.errors.UniqueViolation):
            printBoldRed(f"Encountered {error}, {type(error)} for statement {updateStatement}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
        return False
    return True


def updateRole(username: str, role_name: str, db_name: str) -> bool:
    global schema_name
    aConnection = None
    updateStatement = ''
    try:
        aConnection = psycopg2.connect(user="postgres",
                                       password="postgres",
                                       host="127.0.0.1",
                                       port="5432",
                                       database=db_name)
        # printBoldYellow(f"Established database connection with {db_name} in Persistence")
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()
        # Use parameterized query to prevent SQL injection
        updateStatement = sql.SQL("UPDATE {schema}.users SET role_name = %s WHERE username = %s").format(
            schema=sql.Identifier(schema_name)
        )
        aCursor.execute(updateStatement, (role_name, username))
        aConnection.commit()
        aCursor.close()
        aConnection.close()
    except Exception as error:
        if not isinstance(error, psycopg2.errors.UniqueViolation):
            printBoldRed(f"Encountered {error}, {type(error)} for statement {updateStatement}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
        return False
    return True


def inactivateUserInDatabase(username: str, db_name: str) -> bool:
    global schema_name
    aConnection = None
    updateStatement = ''
    try:
        aConnection = psycopg2.connect(user="postgres",
                                       password="postgres",
                                       host="127.0.0.1",
                                       port="5432",
                                       database=db_name)
        # printBoldYellow(f"Established database connection with {db_name} in Persistence")
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()
        # Use parameterized query to prevent SQL injection
        updateStatement = sql.SQL("UPDATE {schema}.users SET active_status = 'NO' WHERE username = %s").format(
            schema=sql.Identifier(schema_name)
        )
        aCursor.execute(updateStatement, (username,))
        aConnection.commit()
        aCursor.close()
        aConnection.close()
    except Exception as error:
        if not isinstance(error, psycopg2.errors.UniqueViolation):
            printBoldRed(f"Encountered {error}, {type(error)} for statement {updateStatement}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
        return False
    return True


def activateInactiveUserInDatabase(username: str, db_name: str):
    global schema_name
    aConnection = None
    updateStatement = ''
    try:
        aConnection = psycopg2.connect(user="postgres",
                                       password="postgres",
                                       host="127.0.0.1",
                                       port="5432",
                                       database=db_name)
        # printBoldYellow(f"Established database connection with {db_name} in Persistence")
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()
        # Use parameterized query to prevent SQL injection
        updateStatement = sql.SQL("UPDATE {schema}.users SET active_status = 'YES' WHERE username = %s").format(
            schema=sql.Identifier(schema_name)
        )
        aCursor.execute(updateStatement, (username,))
        aConnection.commit()
        aCursor.close()
        aConnection.close()
    except Exception as error:
        if not isinstance(error, psycopg2.errors.UniqueViolation):
            printBoldRed(f"Encountered {error}, {type(error)} for statement {updateStatement}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
        return False
    return True


# ************************* END - USERS TABLE ******************************************

# ************************* BEGIN - DATA TABLE ******************************************

def rationaliseOK_NotOK(input: Union[str, bool], includeNotCheckedAsReturnValue: bool = False):
    # a step skipped by the model's Machine Setting "Bypass Component Press and Second Tightening" keeps its result
    if isinstance(input, str) and input.strip().lower() == bypassedResult.lower():
        return bypassedResult
    if not includeNotCheckedAsReturnValue:
        if input is None:
            return notok.upper()
        if isinstance(input, str):
            if input.strip().lower() == "ok":
                return ok.upper()
            if (input.strip().lower() == "notok") or (input.strip().lower() == "not ok"):
                return notok.upper()
            return notok.upper()
        if isinstance(input, bool):
            if input:
                return ok.upper()
            else:
                return notok.upper()
        return notok.upper()
    else:
        if input is None:
            return notChecked
        if isinstance(input, str):
            if input.strip().lower() == "ok":
                return ok.upper()
            if (input.strip().lower() == "notok") or (input.strip().lower() == "not ok"):
                return notok.upper()
            return notChecked
        if isinstance(input, bool):
            if input:
                return ok.upper()
            else:
                return notok.upper()
        return notChecked


def getUniqueModelNames(db_name: str) -> List[str]:
    """
    Retrieve unique model names from the hub_and_disc_assembly_data table.

    Args:
        db_name (str): Database name

    Returns:
        List[str]: List of unique model names.

    Raises:
        Exception: No exceptions are raised. Consider raising if database connection or query fails.
    """
    model_names = []
    try:
        # Establish connection to PostgreSQL database
        with psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1",
                              port="5432", database=db_name) as conn:
            with conn.cursor() as cursor:
                # Execute query to get distinct model names
                query = "SELECT DISTINCT model_name FROM al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data;"
                cursor.execute(query)

                # Fetch all results
                results = cursor.fetchall()

                # Extract model names from results (single column)
                model_names = [row[0] for row in results if row[0] != 'Not Populated']

    except Error:
        # print(f"Database error: {e}")
        # raise Exception(f"Failed to fetch model names: {e}")
        return []
    except Exception:
        # print(f"Unexpected error: {e}")
        # raise Exception(f"Unexpected error: {e}")
        return []

    return model_names


def checkIfQRCodeExists(qr_code: str, db_name: str) -> bool:
    """
    Check if a given QR code exists in the hub_and_disc_assembly_data table.

    Args:
        db_name (str): Database name
        qr_code (str): QR code to check.

    Returns:
        bool: True if QR code exists, False otherwise.

    Raises:
        Exception: No exceptions are raised. Consider raising if database connection or query fails.
    """
    try:
        # Validate input
        if not qr_code or not isinstance(qr_code, str):
            return False

        # Establish connection to PostgreSQL database
        # connect_timeout: a PostgreSQL that does not answer must not hold up the QR code server's scan for ever.
        # closing(): psycopg2's own "with connection" only ends the transaction, it does not close the connection
        with closing(psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1",
                                      port="5432", database=db_name, connect_timeout=5)) as conn, conn:
            with conn.cursor() as cursor:
                # Execute query to check QR code existence
                query = """
                    SELECT EXISTS (
                        SELECT 1 
                        FROM al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data 
                        WHERE qr_code = %s
                    );
                """
                cursor.execute(query, (qr_code,))

                # Fetch result (returns True/False)
                exists = cursor.fetchone()[0]

                return exists

    except Error:
        # print(f"Database error: {e}")
        return False
    except ValueError:
        # print(f"Input error: {e}")
        return False
    except Exception:
        # print(f"Unexpected error: {e}")
        # raise Exception(f"Unexpected error: {e}")
        return False


def reindexIndices(db_name: str) -> bool:
    """
    Reindex both idx_model_name and idx_qr_code for hub_and_disc_assembly_data table.

    Args:
        db_name (str): Database name.

    Returns:
        bool: True if reindexing succeeds, False otherwise.

    Raises:
        Exception: No exceptions are raised. Consider raising if database connection or query fails.
    """
    try:
        # Establish connection to PostgreSQL database
        with psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1",
                              port="5432", database=db_name) as conn:
            # Set autocommit to handle REINDEX
            conn.autocommit = True
            with conn.cursor() as cursor:
                # Reindex both indices
                query = """
                    REINDEX INDEX al_hub_and_disc_assembly_schema.idx_model_name;
                    REINDEX INDEX al_hub_and_disc_assembly_schema.idx_qr_code;
                    REINDEX INDEX al_hub_and_disc_assembly_schema.idx_created_on;
                """
                cursor.execute(query)
                # print("Successfully reindexed idx_model_name, idx_qr_code, and idx_created_on")
                return True

    except Error:
        # print(f"Database error during reindexing: {e}")
        # raise Exception(f"Failed to reindex indices: {e}")
        return False
    except Exception:
        # print(f"Unexpected error: {e}")
        # raise Exception(f"Unexpected error: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.autocommit = False  # Restore default autocommit state


def reindexQRCodeIndex(db_name: str) -> bool:
    """
    Reindex idx_qr_code for hub_and_disc_assembly_data table.

    Args:
        db_name (str): Database name.

    Returns:
        bool: True if reindexing succeeds, False otherwise.

    Raises:
        Exception: No exceptions are raised. Consider raising if database connection or query fails.
    """
    try:
        # Establish connection to PostgreSQL database
        with psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1",
                              port="5432", database=db_name) as conn:
            # Set autocommit to handle REINDEX
            conn.autocommit = True
            with conn.cursor() as cursor:
                # Reindex both indices
                query = """
                    REINDEX INDEX al_hub_and_disc_assembly_schema.idx_qr_code;
                """
                cursor.execute(query)
                # print("Successfully reindexed idx_model_name, idx_qr_code, and idx_created_on")
                return True

    except Error:
        # print(f"Database error during reindexing: {e}")
        # raise Exception(f"Failed to reindex indices: {e}")
        return False
    except Exception:
        # print(f"Unexpected error: {e}")
        # raise Exception(f"Unexpected error: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.autocommit = False  # Restore default autocommit state


def insertData(
        db_name: str,
        qr_code: str,
        model_name: str = "Not Populated",
        lhs_rhs: str = "Not Populated",
        model_tonnage: str = "Not Populated",

        component_assembly_start_datetime: str = "",

        check_knuckle_imagefile: str = "Not Populated",
        check_knuckle_result: str = "Not Checked",
        check_knuckle_datetime: str = "",

        check_hub_and_bottom_bearing_imagefile: str = "Not Populated",
        check_hub_and_bottom_bearing_result: str = "Not Checked",
        check_hub_and_bottom_bearing_datetime: str = "",

        check_top_bearing_imagefile: str = "Not Populated",
        check_top_bearing_result: str = "Not Checked",
        check_top_bearing_datetime: str = "",

        check_nut_and_platewasher_imagefile: str = "Not Populated",
        check_nut_and_platewasher_result: str = "Not Checked",
        check_nut_and_platewasher_datetime: str = "",

        nut_tightening_torque_1: float = -1.0,
        nut_tightening_torque_1_result: str = "Not Checked",
        nut_tightening_torque_1_datetime: str = "",


        check_bung_for_component_press_imagefile: str = "Not Populated",
        check_bung_for_component_press_result: str = "Not Checked",
        check_bung_for_component_press_datetime: str = "",

        component_press_done: str = "Not Checked",
        component_press_datetime: str = "",

        check_no_bung_imagefile: str = "Not Populated",
        check_no_bung_result: str = "Not Checked",
        check_no_bung_datetime: str = "",

        nut_tightening_torque_2: float = -1.0,
        nut_tightening_torque_2_result: str = "Not Checked",
        nut_tightening_torque_2_datetime: str = "",

        check_splitpin_and_washer_imagefile: str = "Not Populated",
        check_splitpin_and_washer_result: str = "Not Checked",
        check_splitpin_and_washer_datetime: str = "",

        check_cap_imagefile: str = "Not Populated",
        check_cap_result: str = "Not Checked",
        check_cap_datetime: str = "",

        check_bung_cap_press_imagefile: str = "Not Populated",
        check_bung_cap_press_result: str = "Not Checked",
        check_bung_cap_press_datetime: str = "",

        cap_press_done: str = "Not Checked",
        cap_press_datetime: str = "",

        free_rotation_torque_1: float = -1.0,
        free_rotation_torque_1_result: str = "Not Checked",
        free_rotation_torque_1_datetime: str = "",

        setting_rotation_rpm: Optional[float] = None,
        setting_tightening_torque1_setpoint: Optional[float] = None,
        setting_tightening_torque2_setpoint: Optional[float] = None,
        setting_no_of_rotation1_ccw: Optional[float] = None,
        setting_no_of_rotation1_cw: Optional[float] = None,
        setting_rotation_order: Optional[str] = None,
        # rotation during tightening done (PLC_PC_Station2RotationDuringTighteningDone) at torque 1 / torque 2
        nut_tightening_torque_1_rotation_done: Optional[bool] = None,
        nut_tightening_torque_2_rotation_done: Optional[bool] = None,
        # the cycle was aborted (emergency button / manual abort) and why
        aborted: bool = False,
        abort_reason: Optional[str] = None,
        bypassed_press_and_tightening2: bool = False,

        ok_notok_result: str = "Not OK",
        username: str = "Default User",
        remarks: str = ""
) -> bool:
    """
    Insert one complete or partial record into hub_and_disc_assembly_data.
    All *_datetime fields are optional — passed as empty string = NULL in DB.
    Uses safe parameterized query.
    """
    global schema_name

    if not qr_code or qr_code.strip() == "":
        return False

    # Normalize result fields (OK / Not OK / Not Checked)
    ok_notok_result = rationaliseOK_NotOK(ok_notok_result)

    result_fields = [
        check_knuckle_result,
        check_hub_and_bottom_bearing_result,
        check_top_bearing_result,
        check_nut_and_platewasher_result,
        nut_tightening_torque_1_result,
        check_bung_for_component_press_result,
        component_press_done,
        check_no_bung_result,
        nut_tightening_torque_2_result,
        check_splitpin_and_washer_result,
        check_cap_result,
        check_bung_cap_press_result,
        cap_press_done,
        free_rotation_torque_1_result,
    ]

    normalized_results = [
        rationaliseOK_NotOK(val, includeNotCheckedAsReturnValue=True)
        for val in result_fields
    ]

    (
        check_knuckle_result,
        check_hub_and_bottom_bearing_result,
        check_top_bearing_result,
        check_nut_and_platewasher_result,
        nut_tightening_torque_1_result,
        check_bung_for_component_press_result,
        component_press_done,
        check_no_bung_result,
        nut_tightening_torque_2_result,
        check_splitpin_and_washer_result,
        check_cap_result,
        check_bung_cap_press_result,
        cap_press_done,
        free_rotation_torque_1_result,
    ) = normalized_results

    # Coerce torque values to float, fallback to -1.0
    try:
        nut_tightening_torque_1 = float(nut_tightening_torque_1)
    except (TypeError, ValueError):
        nut_tightening_torque_1 = -1.0

    try:
        nut_tightening_torque_2 = float(nut_tightening_torque_2)
    except (TypeError, ValueError):
        nut_tightening_torque_2 = -1.0

    try:
        free_rotation_torque_1 = float(free_rotation_torque_1)
    except (TypeError, ValueError):
        free_rotation_torque_1 = -1.0

    conn = None
    try:
        conn = psycopg2.connect(
            user="postgres",
            password="postgres",
            host="127.0.0.1",
            port="5432",
            database=db_name
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()

        # ─────────────────────────────────────────────────────
        #  Helper to prepare timestamp SQL + parameter
        # ─────────────────────────────────────────────────────
        # def ts_placeholder(dt_str: str) -> tuple[str, list[str]]:
        #     if not dt_str or not dt_str.strip():
        #         return "NULL", []
        #     try:
        #         pg_fmt = getPostgresDatetimeFromString(dt_str.strip())
        #         return "to_timestamp(%s, 'DD-MM-YYYY HH24:MI:SS')", [pg_fmt]
        #     except ValueError:
        #         return "NULL", []

        def ts_placeholder(dt_str: str) -> tuple[str, list[str]]:
            if not dt_str or not dt_str.strip():
                return "NULL", []
            try:
                from BaseUtils import convertToPostgresTimestamp
                pg_fmt = convertToPostgresTimestamp(dt_str.strip())
                return "%s::timestamp", [pg_fmt]
            except ValueError:
                return "NULL", []

        params: list[Any] = []
        placeholders: list[str] = []

        # Core fields
        params.extend([qr_code, model_name, lhs_rhs, model_tonnage])
        placeholders.extend(["%s"] * 4)

        # component_assembly_start_datetime
        sql_part, p = ts_placeholder(component_assembly_start_datetime)
        placeholders.append(sql_part)
        params.extend(p)

        # ── All checking / pressing / torque steps ───────────
        step_data = [
            # image/result/datetime triples
            (check_knuckle_imagefile, check_knuckle_result, check_knuckle_datetime),
            (check_hub_and_bottom_bearing_imagefile, check_hub_and_bottom_bearing_result,
             check_hub_and_bottom_bearing_datetime),
            (check_top_bearing_imagefile, check_top_bearing_result, check_top_bearing_datetime),
            (check_nut_and_platewasher_imagefile, check_nut_and_platewasher_result, check_nut_and_platewasher_datetime),
            (nut_tightening_torque_1, nut_tightening_torque_1_result, nut_tightening_torque_1_datetime),
            (check_bung_for_component_press_imagefile, check_bung_for_component_press_result,
             check_bung_for_component_press_datetime),
            # result + datetime pair
            (component_press_done, component_press_datetime),
            (check_no_bung_imagefile, check_no_bung_result, check_no_bung_datetime),
            (nut_tightening_torque_2, nut_tightening_torque_2_result, nut_tightening_torque_2_datetime),
            (check_splitpin_and_washer_imagefile, check_splitpin_and_washer_result, check_splitpin_and_washer_datetime),
            (check_cap_imagefile, check_cap_result, check_cap_datetime),
            (check_bung_cap_press_imagefile, check_bung_cap_press_result, check_bung_cap_press_datetime),
            # result + datetime pair
            (cap_press_done, cap_press_datetime),
            # back to value/result/datetime triple
            (free_rotation_torque_1, free_rotation_torque_1_result, free_rotation_torque_1_datetime),
        ]

        for item in step_data:
            if len(item) == 3:  # value/result + datetime
                val, res, dt = item
                params.extend([val, res])
                placeholders.extend(["%s", "%s"])

                sql_part, p = ts_placeholder(dt)
                placeholders.append(sql_part)
                params.extend(p)

            else:  # only result + datetime
                res, dt = item
                params.append(res)
                placeholders.append("%s")

                sql_part, p = ts_placeholder(dt)
                placeholders.append(sql_part)
                params.extend(p)

        # Machine settings written to the PLC for this component (NULL when not written)
        for v in (setting_rotation_rpm, setting_tightening_torque1_setpoint, setting_tightening_torque2_setpoint,
                  setting_no_of_rotation1_ccw, setting_no_of_rotation1_cw):
            params.append(None if v is None else float(v))
            placeholders.append("%s")
        params.append(str(setting_rotation_order)[:10] if setting_rotation_order else None)     # CW.CCW / CCW.CW
        placeholders.append("%s")
        # Rotation during tightening done at torque 1 / 2 (NULL when not provided by the PLC)
        for v in (nut_tightening_torque_1_rotation_done, nut_tightening_torque_2_rotation_done):
            params.append(None if v is None else bool(v))
            placeholders.append("%s")
        # Aborted cycle (emergency button / manual abort) and why; bung / press / second tightening bypassed
        params.extend([bool(aborted), (abort_reason or None) if aborted else None, bool(bypassed_press_and_tightening2)])
        placeholders.extend(["%s", "%s", "%s"])

        # Final fields
        params.extend([ok_notok_result, username, remarks])
        placeholders.extend(["%s"] * 3)

        # created_on ← server time
        placeholders.append("NOW()")

        # ── Build & execute ──────────────────────────────────
        query = sql.SQL("""
            INSERT INTO {schema}.hub_and_disc_assembly_data (
                qr_code, model_name, lhs_rhs, model_tonnage, component_assembly_start_datetime,
                check_knuckle_imagefile,     check_knuckle_result,     check_knuckle_datetime,
                check_hub_and_bottom_bearing_imagefile, check_hub_and_bottom_bearing_result, check_hub_and_bottom_bearing_datetime,
                check_top_bearing_imagefile, check_top_bearing_result, check_top_bearing_datetime,
                check_nut_and_platewasher_imagefile, check_nut_and_platewasher_result, check_nut_and_platewasher_datetime,
                nut_tightening_torque_1, nut_tightening_torque_1_result, nut_tightening_torque_1_datetime,
                check_bung_for_component_press_imagefile, check_bung_for_component_press_result, check_bung_for_component_press_datetime,
                component_press_done, component_press_datetime,
                check_no_bung_imagefile, check_no_bung_result, check_no_bung_datetime,
                nut_tightening_torque_2, nut_tightening_torque_2_result, nut_tightening_torque_2_datetime,
                check_splitpin_and_washer_imagefile, check_splitpin_and_washer_result, check_splitpin_and_washer_datetime,
                check_cap_imagefile, check_cap_result, check_cap_datetime,
                check_bung_cap_press_imagefile, check_bung_cap_press_result, check_bung_cap_press_datetime,
                cap_press_done, cap_press_datetime,
                free_rotation_torque_1, free_rotation_torque_1_result, free_rotation_torque_1_datetime,
                setting_rotation_rpm, setting_tightening_torque1_setpoint, setting_tightening_torque2_setpoint,
                setting_no_of_rotation1_ccw, setting_no_of_rotation1_cw, setting_rotation_order,
                nut_tightening_torque_1_rotation_done, nut_tightening_torque_2_rotation_done,
                aborted, abort_reason, bypassed_press_and_tightening2,
                ok_notok_result, username, remarks, created_on
            ) VALUES ({ph})
        """).format(
            schema=sql.Identifier(schema_name),
            ph=sql.SQL(", ").join(sql.SQL(p) for p in placeholders)
        )

        cur.execute(query, params)
        conn.commit()
        # No REINDEX here (it ran after every insert before 2026-11-06). PostgreSQL updates every index of the table
        # in the insert's own transaction, so the new QR code is found through idx_qr_code as soon as the insert is
        # committed; REINDEX only rebuilds an index (after corruption or heavy bloat), and while it runs it blocks
        # inserts and the reads that would use the index. reindexIndices() is kept for maintenance by hand.
        return True

    except psycopg2.errors.UniqueViolation:
        # QR code already exists
        return False
    except Exception as e:
        print(f"Insert failed: {type(e).__name__}: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


all_columns_record_header = [
    "qr_code",
    "model_name",
    "lhs_rhs",
    "model_tonnage",
    "component_assembly_start_datetime",
    "check_knuckle_imagefile",
    "check_knuckle_result",
    "check_knuckle_datetime",
    "check_hub_and_bottom_bearing_imagefile",
    "check_hub_and_bottom_bearing_result",
    "check_hub_and_bottom_bearing_datetime",
    "check_top_bearing_imagefile",
    "check_top_bearing_result",
    "check_top_bearing_datetime",
    "check_nut_and_platewasher_imagefile",
    "check_nut_and_platewasher_result",
    "check_nut_and_platewasher_datetime",
    "nut_tightening_torque_1",
    "nut_tightening_torque_1_result",
    "nut_tightening_torque_1_datetime",
    "check_bung_for_component_press_imagefile",
    "check_bung_for_component_press_result",
    "check_bung_for_component_press_datetime",
    "component_press_done",
    "component_press_datetime",
    "check_no_bung_imagefile",
    "check_no_bung_result",
    "check_no_bung_datetime",
    "nut_tightening_torque_2",
    "nut_tightening_torque_2_result",
    "nut_tightening_torque_2_datetime",
    "check_splitpin_and_washer_imagefile",
    "check_splitpin_and_washer_result",
    "check_splitpin_and_washer_datetime",
    "check_cap_imagefile",
    "check_cap_result",
    "check_cap_datetime",
    "check_bung_cap_press_imagefile",
    "check_bung_cap_press_result",
    "check_bung_cap_press_datetime",
    "cap_press_done",
    "cap_press_datetime",
    "free_rotation_torque_1",
    "free_rotation_torque_1_result",
    "free_rotation_torque_1_datetime",
    "setting_rotation_rpm",
    "setting_tightening_torque1_setpoint",
    "setting_tightening_torque2_setpoint",
    "setting_no_of_rotation1_ccw",
    "setting_no_of_rotation1_cw",
    "setting_rotation_order",
    "nut_tightening_torque_1_rotation_done",
    "nut_tightening_torque_2_rotation_done",
    "aborted",
    "abort_reason",
    "bypassed_press_and_tightening2",
    "ok_notok_result",
    "username",
    "remarks",
    "created_on"
]

# SQL query with parameterized model_name
select_query_by_model_name = """
SELECT 
    qr_code,
    model_name,
    lhs_rhs,
    model_tonnage,
    component_assembly_start_datetime,
    check_knuckle_imagefile,
    check_knuckle_result,
    check_knuckle_datetime,
    check_hub_and_bottom_bearing_imagefile,
    check_hub_and_bottom_bearing_result,
    check_hub_and_bottom_bearing_datetime,
    check_top_bearing_imagefile,
    check_top_bearing_result,
    check_top_bearing_datetime,
    check_nut_and_platewasher_imagefile,
    check_nut_and_platewasher_result,
    check_nut_and_platewasher_datetime,
    nut_tightening_torque_1,
    nut_tightening_torque_1_result,
    nut_tightening_torque_1_datetime,
    check_bung_for_component_press_imagefile,
    check_bung_for_component_press_result,
    check_bung_for_component_press_datetime,
    component_press_done,
    component_press_datetime,
    check_no_bung_imagefile,
    check_no_bung_result,
    check_no_bung_datetime,
    nut_tightening_torque_2,
    nut_tightening_torque_2_result,
    nut_tightening_torque_2_datetime,
    check_splitpin_and_washer_imagefile,
    check_splitpin_and_washer_result,
    check_splitpin_and_washer_datetime,
    check_cap_imagefile,
    check_cap_result,
    check_cap_datetime,
    check_bung_cap_press_imagefile,
    check_bung_cap_press_result,
    check_bung_cap_press_datetime,
    cap_press_done,
    cap_press_datetime,
    free_rotation_torque_1,
    free_rotation_torque_1_result,
    free_rotation_torque_1_datetime,
    setting_rotation_rpm,
    setting_tightening_torque1_setpoint,
    setting_tightening_torque2_setpoint,
    setting_no_of_rotation1_ccw,
    setting_no_of_rotation1_cw,
    setting_rotation_order,
    nut_tightening_torque_1_rotation_done,
    nut_tightening_torque_2_rotation_done,
    aborted,
    abort_reason,
    bypassed_press_and_tightening2,
    ok_notok_result,
    username,
    remarks,
    created_on
FROM {schema}.hub_and_disc_assembly_data
WHERE model_name = %s;
"""


# ************************************ REPORTS ****************************************

def getDataByModelName(modelName: str, db_name: str, debug: bool = False) -> str:
    global schema_name
    """
        Fetch records from hub_and_disc_assembly_schema.hub_and_disc_assembly_data for a given model_name
        and return them as a CSV string with column names as the header.

        Args:
            modelName (str): The model name to filter records (required)
            db_name (str): Database name

        Returns:
            str: CSV string with column names as header and records as rows, or empty string on error
        """

    csv_output = StringIO()
    # Create CSV writer
    writer = csv.writer(csv_output, quoting=csv.QUOTE_MINIMAL)

    if not modelName or not isinstance(modelName, str) or not modelName.strip():
        printBoldRed("Error: model_name must be a non-empty string")  # Replace printBoldRed
        writer.writerow(all_columns_record_header)
        return csv_output.getvalue()

    aConnection = None
    aCursor = None
    csv_string = ",".join(all_columns_record_header)

    try:
        # Establish database connection
        aConnection = psycopg2.connect(
            user="postgres",
            password="postgres",
            host="127.0.0.1",
            port="5432",
            database=db_name
        )
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()

        # Execute query

        # FIXED: Use parameterized query to prevent SQL injection
        current_select_query = sql.SQL(select_query_by_model_name).format(
            schema=sql.Identifier(schema_name)
        )
        if debug:
            printBoldGreen(f"Executing query for model {modelName}")
        aCursor.execute(current_select_query, (modelName,))
        records = aCursor.fetchall()

        # Get column names from cursor description
        columns = [desc[0] for desc in aCursor.description]

        # Write header
        writer.writerow(columns)

        # Write records
        for record in records:
            # Format each field to handle types (e.g., timestamps, NULLs)
            formatted_record = []
            for value in record:
                if value is None:
                    formatted_record.append("")
                elif isinstance(value, datetime):
                    formatted_record.append(value.strftime("%Y-%m-%d %H:%M:%S"))
                else:
                    formatted_record.append(str(value))
            writer.writerow(formatted_record)

        # Get CSV string
        csv_string = csv_output.getvalue()

    except Exception as error:
        printBoldRed(f"Encountered {error}, {type(error)}")  # Replace printBoldRed
        writer.writerow(all_columns_record_header)
        return csv_output.getvalue()
    finally:
        if aCursor:
            try:
                aCursor.close()
            except Exception:
                noteSuppressed()
        if aConnection:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
        csv_output.close()
    if debug:
        printBoldYellow(csv_string)
    return csv_string


# SQL query with parameterized model_name and date range
select_query_by_model_name_and_date_limits = """
SELECT 
    qr_code,
    model_name,
    lhs_rhs,
    model_tonnage,
    component_assembly_start_datetime,
    check_knuckle_imagefile,
    check_knuckle_result,
    check_knuckle_datetime,
    check_hub_and_bottom_bearing_imagefile,
    check_hub_and_bottom_bearing_result,
    check_hub_and_bottom_bearing_datetime,
    check_top_bearing_imagefile,
    check_top_bearing_result,
    check_top_bearing_datetime,
    check_nut_and_platewasher_imagefile,
    check_nut_and_platewasher_result,
    check_nut_and_platewasher_datetime,
    nut_tightening_torque_1,
    nut_tightening_torque_1_result,
    nut_tightening_torque_1_datetime,
    check_bung_for_component_press_imagefile,
    check_bung_for_component_press_result,
    check_bung_for_component_press_datetime,
    component_press_done,
    component_press_datetime,
    check_no_bung_imagefile,
    check_no_bung_result,
    check_no_bung_datetime,
    nut_tightening_torque_2,
    nut_tightening_torque_2_result,
    nut_tightening_torque_2_datetime,
    check_splitpin_and_washer_imagefile,
    check_splitpin_and_washer_result,
    check_splitpin_and_washer_datetime,
    check_cap_imagefile,
    check_cap_result,
    check_cap_datetime,
    check_bung_cap_press_imagefile,
    check_bung_cap_press_result,
    check_bung_cap_press_datetime,
    cap_press_done,
    cap_press_datetime,
    free_rotation_torque_1,
    free_rotation_torque_1_result,
    free_rotation_torque_1_datetime,
    setting_rotation_rpm,
    setting_tightening_torque1_setpoint,
    setting_tightening_torque2_setpoint,
    setting_no_of_rotation1_ccw,
    setting_no_of_rotation1_cw,
    setting_rotation_order,
    nut_tightening_torque_1_rotation_done,
    nut_tightening_torque_2_rotation_done,
    aborted,
    abort_reason,
    bypassed_press_and_tightening2,
    ok_notok_result,
    username,
    remarks,
    created_on
FROM {}.hub_and_disc_assembly_data
WHERE model_name = '{}'
  AND created_on >= to_timestamp('{} 00:00:00', 'YYYY-MM-DD HH24:MI:SS')
  AND created_on <  to_timestamp('{} 23:59:59', 'YYYY-MM-DD HH24:MI:SS')
  -- or use: AND created_on::date BETWEEN '{}' AND '{}'
ORDER BY created_on ASC;
"""


def getDataByModelNameAndDateLimits(
        db_name: str,
        modelName: str,
        startDate: str | None,
        endDate: str | None = None,
        debug: bool = False
) -> str:
    """
    Fetch records from hub_and_disc_assembly_schema.hub_and_disc_assembly_data for a given model_name
    and date range, returning them as a CSV string with column names as the header.

    FIXED: Uses parameterized queries and proper date handling to prevent SQL injection and date parsing errors.

    Args:
        db_name: Database name
        modelName: The model name to filter records (required)
        startDate: Start date for created_on (format: YYYY-MM-DD), None for today
        endDate: End date for created_on (format: YYYY-MM-DD), None for today
        debug: Whether to print debug statements

    Returns:
        str: CSV string with column names as header and records as rows, or empty string on error
    """
    global schema_name
    csv_output = StringIO()
    writer = csv.writer(csv_output, quoting=csv.QUOTE_MINIMAL)

    # Validate model_name
    if not modelName or not isinstance(modelName, str) or not modelName.strip():
        printBoldRed("Error: model_name must be a non-empty string")
        writer.writerow(all_columns_record_header)
        return csv_output.getvalue()

    # Parse and validate dates
    # FIXED: Proper handling of None dates - use today's date instead of 23:59:59
    try:
        if startDate:
            start_dt = parse(startDate).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            # Default to today at 00:00:00
            start_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        if endDate:
            end_dt = parse(endDate).replace(hour=23, minute=59, second=59, microsecond=999999)
        else:
            # Default to today at 23:59:59
            end_dt = datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999)
    except (ValueError, TypeError) as e:
        printBoldRed(f"Error parsing dates: {e}")
        writer.writerow(all_columns_record_header)
        return csv_output.getvalue()

    # Swap if start_date > end_date
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    aConnection = None
    aCursor = None
    csv_string = ",".join(all_columns_record_header)

    try:
        # Establish database connection
        aConnection = psycopg2.connect(
            user="postgres",
            password="postgres",
            host="127.0.0.1",
            port="5432",
            database=db_name
        )
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()

        # FIXED: Use parameterized query with proper date handling
        # Use created_on::date for date-only comparison
        currentQuery = sql.SQL("""
            SELECT 
                qr_code, model_name, lhs_rhs, model_tonnage, component_assembly_start_datetime,
                check_knuckle_imagefile, check_knuckle_result, check_knuckle_datetime,
                check_hub_and_bottom_bearing_imagefile, check_hub_and_bottom_bearing_result, check_hub_and_bottom_bearing_datetime,
                check_top_bearing_imagefile, check_top_bearing_result, check_top_bearing_datetime,
                check_nut_and_platewasher_imagefile, check_nut_and_platewasher_result, check_nut_and_platewasher_datetime,
                nut_tightening_torque_1, nut_tightening_torque_1_result, nut_tightening_torque_1_datetime,
                check_bung_for_component_press_imagefile, check_bung_for_component_press_result, check_bung_for_component_press_datetime,
                component_press_done, component_press_datetime,
                check_no_bung_imagefile, check_no_bung_result, check_no_bung_datetime,
                nut_tightening_torque_2, nut_tightening_torque_2_result, nut_tightening_torque_2_datetime,
                check_splitpin_and_washer_imagefile, check_splitpin_and_washer_result, check_splitpin_and_washer_datetime,
                check_cap_imagefile, check_cap_result, check_cap_datetime,
                check_bung_cap_press_imagefile, check_bung_cap_press_result, check_bung_cap_press_datetime,
                cap_press_done, cap_press_datetime,
                free_rotation_torque_1, free_rotation_torque_1_result, free_rotation_torque_1_datetime,
                setting_rotation_rpm, setting_tightening_torque1_setpoint, setting_tightening_torque2_setpoint,
                setting_no_of_rotation1_ccw, setting_no_of_rotation1_cw, setting_rotation_order,
                nut_tightening_torque_1_rotation_done, nut_tightening_torque_2_rotation_done,
                aborted, abort_reason, bypassed_press_and_tightening2,
                ok_notok_result, username, remarks, created_on
            FROM {schema}.hub_and_disc_assembly_data
            WHERE model_name = %s
              AND created_on::date BETWEEN %s AND %s
            ORDER BY created_on ASC
        """).format(schema=sql.Identifier(schema_name))

        if debug:
            printBoldGreen(f"Executing query for model {modelName} between {start_dt.date()} and {end_dt.date()}")

        # Execute with parameterized values
        aCursor.execute(currentQuery, (modelName, start_dt.date(), end_dt.date()))
        records = aCursor.fetchall()

        # Get column names from cursor description
        columns = [desc[0] for desc in aCursor.description]

        # Write header
        writer.writerow(columns)

        # Write records
        for record in records:
            formatted_record = []
            for value in record:
                if value is None:
                    formatted_record.append("")
                elif isinstance(value, datetime):
                    formatted_record.append(value.strftime("%Y-%m-%d %H:%M:%S"))
                else:
                    formatted_record.append(str(value))
            writer.writerow(formatted_record)

        # Get CSV string
        csv_string = csv_output.getvalue()
        if debug:
            printBoldYellow(csv_string)
        return csv_string

    except psycopg2.OperationalError as error:
        printBoldRed(f"Connection error: {error}, {type(error)}")
        return csv_string
    except Exception as error:
        printBoldRed(f"Query error: {error}, {type(error)}")
        return csv_string
    finally:
        if aCursor:
            try:
                aCursor.close()
            except Exception:
                noteSuppressed()
        if aConnection:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
        csv_output.close()


def getDataOfTodayByModelNumber(modelName: str, db_name: str, debug: bool = False) -> str:
    return getDataByModelNameAndDateLimits(modelName=modelName, startDate=None, endDate=None, db_name=db_name,
                                           debug=debug)


# ************************************ PERSISTENT CONNECTION (CHANGES 110) *******************************

class PersistentDBConnection:
    """One connection kept open for a caller that queries often (the DB server's heartbeat every 0.5 s, the frontend's
    production counts every few seconds), instead of a new connection per query. A query that fails (PostgreSQL
    restarted, the connection broken) closes the connection, opens a new one and tries once more; only if that fails
    too is the error raised. Each statement is limited to STATEMENT_TIMEOUT_MS, so a hung server cannot block the
    caller for ever. Not shared between threads: each thread keeps its own instance."""

    STATEMENT_TIMEOUT_MS: int = CosThetaConfigurator.getTuning("db.statement.timeout.ms", 5000, minimum=0)

    def __init__(self):
        self._connection = None
        self._dbName: Optional[str] = None

    def _open(self, db_name: str):
        self.close()
        self._connection = psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1", port="5432",
                                            database=db_name,
                                            options=f"-c statement_timeout={PersistentDBConnection.STATEMENT_TIMEOUT_MS}",
                                            keepalives=1, keepalives_idle=10, keepalives_interval=5, keepalives_count=2)
        self._connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        self._dbName = db_name

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                noteSuppressed()
        self._connection = None
        self._dbName = None

    def fetchone(self, db_name: str, query, params=None):
        """Runs query and returns its first row (reconnecting once if the connection is broken)."""
        for attempt in (1, 2):
            try:
                if self._connection is None or self._connection.closed or self._dbName != db_name:
                    self._open(db_name)
                with self._connection.cursor() as cursor:
                    cursor.execute(query, params)
                    return cursor.fetchone()
            except Exception:
                self.close()
                if attempt == 2:
                    raise

    def check(self, db_name: str) -> bool:
        """True if the database answers (SELECT 1)."""
        try:
            return self.fetchone(db_name, "SELECT 1;") is not None
        except Exception as error:
            printBoldRed(f"Database {db_name} not reachable: {error}")
            return False


def getProductionCounts(connection: PersistentDBConnection, db_name: str) -> tuple:
    """(today, this week, this month) counts of OK components in one query on a kept-open connection; the same counts
    as getComponentsProducedToday / ThisWeek / ThisMonth. Raises on a database error (the caller keeps its last counts)."""
    query = sql.SQL(
        "SELECT COUNT(*) FILTER (WHERE created_on::date = CURRENT_DATE), "
        "COUNT(*) FILTER (WHERE created_on >= date_trunc('week', CURRENT_DATE)), "
        "COUNT(*) FILTER (WHERE created_on >= date_trunc('month', CURRENT_DATE)) "
        "FROM {schema}.hub_and_disc_assembly_data "
        "WHERE ok_notok_result = 'OK' "
        "AND created_on >= LEAST(date_trunc('week', CURRENT_DATE), date_trunc('month', CURRENT_DATE));"
    ).format(schema=sql.Identifier(schema_name))
    row = connection.fetchone(db_name, query)
    return tuple(int(v or 0) for v in row) if row else (0, 0, 0)


# ************************************ PRODUCTION COUNT QUERIES ****************************************

def getComponentsProducedToday(db_name: str, debug: bool = False) -> int:
    """
    Return the count of components (records) whose created_on date is today.

    Uses a simple DATE cast comparison so the query is index-friendly.

    Args:
        db_name: Database name to query.
        debug:   If True, print the count result.

    Returns:
        int: Number of records created today, or 0 on any error.
    """
    global schema_name
    aConnection = None
    aCursor = None
    count: int = 0
    try:
        aConnection = psycopg2.connect(
            user="postgres",
            password="postgres",
            host="127.0.0.1",
            port="5432",
            database=db_name
        )
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()
        query = sql.SQL(
            "SELECT COUNT(*) FROM {schema}.hub_and_disc_assembly_data "
            "WHERE created_on::date = CURRENT_DATE "
            "AND ok_notok_result = 'OK';"
        ).format(schema=sql.Identifier(schema_name))
        aCursor.execute(query)
        row = aCursor.fetchone()
        count = int(row[0]) if row else 0
        if debug:
            printBoldGreen(f"getComponentsProducedToday() = {count}")
    except Exception as error:
        printBoldRed(f"getComponentsProducedToday() error: {error}, {type(error)}")
        count = 0
    finally:
        if aCursor:
            try:
                aCursor.close()
            except Exception:
                noteSuppressed()
        if aConnection:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
    return count


def getComponentsProducedThisWeek(db_name: str, debug: bool = False) -> int:
    """
    Return the count of components (records) created during the current ISO calendar week
    (Monday 00:00:00 through the current moment).

    Uses date_trunc('week', CURRENT_DATE) which PostgreSQL aligns to Monday.

    Args:
        db_name: Database name to query.
        debug:   If True, print the count result.

    Returns:
        int: Number of records created this week, or 0 on any error.
    """
    global schema_name
    aConnection = None
    aCursor = None
    count: int = 0
    try:
        aConnection = psycopg2.connect(
            user="postgres",
            password="postgres",
            host="127.0.0.1",
            port="5432",
            database=db_name
        )
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()
        query = sql.SQL(
            "SELECT COUNT(*) FROM {schema}.hub_and_disc_assembly_data "
            "WHERE created_on >= date_trunc('week', CURRENT_DATE) "
            "AND ok_notok_result = 'OK';"
        ).format(schema=sql.Identifier(schema_name))
        aCursor.execute(query)
        row = aCursor.fetchone()
        count = int(row[0]) if row else 0
        if debug:
            printBoldGreen(f"getComponentsProducedThisWeek() = {count}")
    except Exception as error:
        printBoldRed(f"getComponentsProducedThisWeek() error: {error}, {type(error)}")
        count = 0
    finally:
        if aCursor:
            try:
                aCursor.close()
            except Exception:
                noteSuppressed()
        if aConnection:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
    return count


def getComponentsProducedThisMonth(db_name: str, debug: bool = False) -> int:
    """
    Return the count of components (records) created during the current calendar month
    (first day of month 00:00:00 through the current moment).

    Uses date_trunc('month', CURRENT_DATE) for a clean month boundary.

    Args:
        db_name: Database name to query.
        debug:   If True, print the count result.

    Returns:
        int: Number of records created this month, or 0 on any error.
    """
    global schema_name
    aConnection = None
    aCursor = None
    count: int = 0
    try:
        aConnection = psycopg2.connect(
            user="postgres",
            password="postgres",
            host="127.0.0.1",
            port="5432",
            database=db_name
        )
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()
        query = sql.SQL(
            "SELECT COUNT(*) FROM {schema}.hub_and_disc_assembly_data "
            "WHERE created_on >= date_trunc('month', CURRENT_DATE) "
            "AND ok_notok_result = 'OK';"
        ).format(schema=sql.Identifier(schema_name))
        aCursor.execute(query)
        row = aCursor.fetchone()
        count = int(row[0]) if row else 0
        if debug:
            printBoldGreen(f"getComponentsProducedThisMonth() = {count}")
    except Exception as error:
        printBoldRed(f"getComponentsProducedThisMonth() error: {error}, {type(error)}")
        count = 0
    finally:
        if aCursor:
            try:
                aCursor.close()
            except Exception:
                noteSuppressed()
        if aConnection:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
    return count


# ******************************* END PRODUCTION COUNT QUERIES *********************************

select_query_from_start_date_to_end_date = """
SELECT 
    qr_code,
    model_name,
    lhs_rhs,
    model_tonnage,
    component_assembly_start_datetime,
    check_knuckle_imagefile,
    check_knuckle_result,
    check_knuckle_datetime,
    check_hub_and_bottom_bearing_imagefile,
    check_hub_and_bottom_bearing_result,
    check_hub_and_bottom_bearing_datetime,
    check_top_bearing_imagefile,
    check_top_bearing_result,
    check_top_bearing_datetime,
    check_nut_and_platewasher_imagefile,
    check_nut_and_platewasher_result,
    check_nut_and_platewasher_datetime,
    nut_tightening_torque_1,
    nut_tightening_torque_1_result,
    nut_tightening_torque_1_datetime,
    check_bung_for_component_press_imagefile,
    check_bung_for_component_press_result,
    check_bung_for_component_press_datetime,
    component_press_done,
    component_press_datetime,
    check_no_bung_imagefile,
    check_no_bung_result,
    check_no_bung_datetime,
    nut_tightening_torque_2,
    nut_tightening_torque_2_result,
    nut_tightening_torque_2_datetime,
    check_splitpin_and_washer_imagefile,
    check_splitpin_and_washer_result,
    check_splitpin_and_washer_datetime,
    check_cap_imagefile,
    check_cap_result,
    check_cap_datetime,
    check_bung_cap_press_imagefile,
    check_bung_cap_press_result,
    check_bung_cap_press_datetime,
    cap_press_done,
    cap_press_datetime,
    free_rotation_torque_1,
    free_rotation_torque_1_result,
    free_rotation_torque_1_datetime,
    setting_rotation_rpm,
    setting_tightening_torque1_setpoint,
    setting_tightening_torque2_setpoint,
    setting_no_of_rotation1_ccw,
    setting_no_of_rotation1_cw,
    setting_rotation_order,
    nut_tightening_torque_1_rotation_done,
    nut_tightening_torque_2_rotation_done,
    aborted,
    abort_reason,
    bypassed_press_and_tightening2,
    ok_notok_result,
    username,
    remarks,
    created_on
FROM {}.hub_and_disc_assembly_data
WHERE created_on >= to_timestamp('{} 00:00:00', 'YYYY-MM-DD HH24:MI:SS')
  AND created_on <  to_timestamp('{} 23:59:59.999', 'YYYY-MM-DD HH24:MI:SS')
  -- Alternative (simpler & usually preferred):
  -- AND created_on::date BETWEEN '{}' AND '{}'
ORDER BY model_name ASC, created_on ASC;
"""


def getDataByDateLimits(
        db_name: str,
        startDate: str | None,
        endDate: str | None = None,
        debug: bool = False
) -> str:
    """
    Fetch records from hub_and_disc_assembly_schema.hub_and_disc_assembly_data for a given date range,
    returning them as a CSV string with column names as the header.

    FIXED: Uses parameterized queries and proper date handling to prevent SQL injection and date parsing errors.

    Args:
        db_name: Database name
        startDate: Start date for created_on (format: YYYY-MM-DD), None for today
        endDate: End date for created_on (format: YYYY-MM-DD), None for today

    Returns:
        str: CSV string with column names as header and records as rows, or empty string on error
    """
    global schema_name
    csv_output = StringIO()
    writer = csv.writer(csv_output, quoting=csv.QUOTE_MINIMAL)

    # Parse and validate dates
    # FIXED: Proper handling of None dates - use today's date instead of 23:59:59
    try:
        if startDate:
            start_dt = parse(startDate).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            # Default to today at 00:00:00
            start_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        if endDate:
            end_dt = parse(endDate).replace(hour=23, minute=59, second=59, microsecond=999999)
        else:
            # Default to today at 23:59:59
            end_dt = datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999)
    except (ValueError, TypeError) as e:
        printBoldRed(f"Error parsing dates: {e}")
        writer.writerow(all_columns_record_header)
        return csv_output.getvalue()

    # Swap if start_date > end_date
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    aConnection = None
    aCursor = None
    csv_string = ",".join(all_columns_record_header)

    try:
        # Establish database connection
        aConnection = psycopg2.connect(
            user="postgres",
            password="postgres",
            host="127.0.0.1",
            port="5432",
            database=db_name
        )
        aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        aCursor = aConnection.cursor()

        # FIXED: Use parameterized query with proper date handling
        currentQuery = sql.SQL("""
            SELECT 
                qr_code, model_name, lhs_rhs, model_tonnage, component_assembly_start_datetime,
                check_knuckle_imagefile, check_knuckle_result, check_knuckle_datetime,
                check_hub_and_bottom_bearing_imagefile, check_hub_and_bottom_bearing_result, check_hub_and_bottom_bearing_datetime,
                check_top_bearing_imagefile, check_top_bearing_result, check_top_bearing_datetime,
                check_nut_and_platewasher_imagefile, check_nut_and_platewasher_result, check_nut_and_platewasher_datetime,
                nut_tightening_torque_1, nut_tightening_torque_1_result, nut_tightening_torque_1_datetime,
                check_bung_for_component_press_imagefile, check_bung_for_component_press_result, check_bung_for_component_press_datetime,
                component_press_done, component_press_datetime,
                check_no_bung_imagefile, check_no_bung_result, check_no_bung_datetime,
                nut_tightening_torque_2, nut_tightening_torque_2_result, nut_tightening_torque_2_datetime,
                check_splitpin_and_washer_imagefile, check_splitpin_and_washer_result, check_splitpin_and_washer_datetime,
                check_cap_imagefile, check_cap_result, check_cap_datetime,
                check_bung_cap_press_imagefile, check_bung_cap_press_result, check_bung_cap_press_datetime,
                cap_press_done, cap_press_datetime,
                free_rotation_torque_1, free_rotation_torque_1_result, free_rotation_torque_1_datetime,
                setting_rotation_rpm, setting_tightening_torque1_setpoint, setting_tightening_torque2_setpoint,
                setting_no_of_rotation1_ccw, setting_no_of_rotation1_cw, setting_rotation_order,
                nut_tightening_torque_1_rotation_done, nut_tightening_torque_2_rotation_done,
                aborted, abort_reason, bypassed_press_and_tightening2,
                ok_notok_result, username, remarks, created_on
            FROM {schema}.hub_and_disc_assembly_data
            WHERE created_on::date BETWEEN %s AND %s
            ORDER BY model_name ASC, created_on ASC
        """).format(schema=sql.Identifier(schema_name))

        if debug:
            printBoldGreen(f"Executing query between {start_dt.date()} and {end_dt.date()}")

        # Execute with parameterized values
        aCursor.execute(currentQuery, (start_dt.date(), end_dt.date()))
        records = aCursor.fetchall()

        # Get column names from cursor description
        columns = [desc[0] for desc in aCursor.description]

        # Write header
        writer.writerow(columns)

        # Write records
        for record in records:
            formatted_record = []
            for value in record:
                if value is None:
                    formatted_record.append("")
                elif isinstance(value, datetime):
                    formatted_record.append(value.strftime("%Y-%m-%d %H:%M:%S"))
                else:
                    formatted_record.append(str(value))
            writer.writerow(formatted_record)

        # Get CSV string
        csv_string = csv_output.getvalue()
        if debug:
            printBoldYellow(csv_string)
        return csv_string

    except psycopg2.OperationalError as error:
        printBoldRed(f"Connection error: {error}, {type(error)}")
        return csv_string
    except Exception as error:
        printBoldRed(f"Query error: {error}, {type(error)}")
        return csv_string
    finally:
        if aCursor:
            try:
                aCursor.close()
            except Exception:
                noteSuppressed()
        if aConnection:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
        csv_output.close()


def getAllDataOfToday(db_name: str, debug: bool = False) -> str:
    return getDataByDateLimits(startDate=None, endDate=None, db_name=db_name, debug=debug)


# ************************* END - DATA TABLE ******************************************

# *********************************Database Statements******************************

def setReportMajorDatabaseActions(value=False):
    global reportMajorDatabaseActions
    reportMajorDatabaseActions = value


def setReportMinorDatabaseActions(value=False):
    global reportMinorDatabaseActions
    reportMinorDatabaseActions = value


def encodeString(inputString: str):
    input_string_bytes = inputString.encode("utf-8")
    base64_bytes = base64.b64encode(input_string_bytes)
    base64_string = base64_bytes.decode("utf-8")
    return base64_string


def decodeString(inputString: str):
    base64_bytes = inputString.encode("utf-8")
    input_string_bytes = base64.b64decode(base64_bytes)
    requiredString = input_string_bytes.decode("utf-8")
    return requiredString


# =============================================================================
# Index Rebuilding Utilities
# =============================================================================
# Purpose: Rebuild indexes on frequently queried tables after bulk operations
#          (e.g., large data imports) to maintain query performance.
# =============================================================================

def reindexHubAndDiscAssemblyData(db_name: str, verbose: bool = True) -> bool:
    """
    Rebuilds indexes on the hub_and_disc_assembly_data table.

    This should be called after bulk inserts or when performance degrades.

    Args:
        db_name: Name of the database to operate on
        verbose: Whether to print success/failure messages

    Returns:
        bool: True if successful, False otherwise
    """
    connection = None
    cursor = None
    success = False

    try:
        connection = psycopg2.connect(
            user="postgres",
            password="postgres",
            host="127.0.0.1",
            port="5432",
            database=db_name
        )
        connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = connection.cursor()

        schema = getSchemaName()

        # List of indexes to rebuild (add more if you create additional indexes)
        indexes = [         # noqa: F841 - for the per-index loop below (commented out)
            f"{schema}_hub_and_disc_assembly_data_qr_code_idx",
            f"{schema}_hub_and_disc_assembly_data_model_name_idx",
            f"{schema}_hub_and_disc_assembly_data_created_on_idx",
            # Add any composite indexes if they exist, e.g.:
            # f"{schema}_hub_and_disc_assembly_data_model_created_idx",
        ]

        # Rebuild the table itself (includes all indexes)
        cursor.execute(f"REINDEX TABLE {schema}.hub_and_disc_assembly_data;")

        # Optionally rebuild individual indexes (useful if only some are degraded)
        # for idx_name in indexes:
        #     cursor.execute(f"REINDEX INDEX {idx_name};")

        connection.commit()
        success = True

        if verbose and reportMajorDatabaseActions:
            printBoldGreen(f"Successfully reindexed hub_and_disc_assembly_data in database {db_name}")

    except psycopg2.errors.UndefinedObject as e:
        printBoldRed(f"Some index does not exist - skipping: {e}")
        success = True  # still consider operation successful if table was reindexed
    except psycopg2.OperationalError as e:
        printBoldRed(f"Connection error during reindex: {e}")
    except Exception as e:
        printBoldRed(f"Error during reindex of hub_and_disc_assembly_data: {e}")
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                noteSuppressed()
        if connection:
            try:
                connection.close()
            except Exception:
                noteSuppressed()

    return success


def ensureIndexesExist(db_name: str, verbose: bool = True) -> bool:
    """
    Creates commonly used indexes if they do not already exist.
    Call this once during database initialization (e.g. in createAllTables or setDatabaseName).
    """
    connection = None
    cursor = None
    success = False

    try:
        connection = psycopg2.connect(
            user="postgres",
            password="postgres",
            host="127.0.0.1",
            port="5432",
            database=db_name
        )
        connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = connection.cursor()

        schema = getSchemaName()
        table = f"{schema}.hub_and_disc_assembly_data"

        # One index per column (2026-11-06). This function used to create a second index on each of these columns
        # under long names that PostgreSQL cuts to 63 characters, next to idx_qr_code / idx_model_name /
        # idx_created_on from the table DDL: every insert then maintained two indexes per column. The duplicates are
        # dropped here (identifiers longer than 63 characters are cut the same way in DROP INDEX).
        for duplicate in ("qr_code", "model_name", "created_on"):
            cursor.execute(f"DROP INDEX IF EXISTS {schema}.{schema}_hub_and_disc_assembly_data_{duplicate}_idx;")

        index_definitions = [
            # Fast lookups by QR code (duplicate check of every scan)
            ("idx_qr_code", f"CREATE INDEX IF NOT EXISTS idx_qr_code ON {table} (qr_code);"),

            # Filter by model (very common in reports)
            ("idx_model_name", f"CREATE INDEX IF NOT EXISTS idx_model_name ON {table} (model_name);"),

            # Time-based queries & sorting
            ("idx_created_on", f"CREATE INDEX IF NOT EXISTS idx_created_on ON {table} (created_on);"),

            # Optional composite index for frequent model + date queries
            # (f"{schema}_hub_and_disc_assembly_data_model_created_idx",
            #  f"CREATE INDEX IF NOT EXISTS {schema}_hub_and_disc_assembly_data_model_created_idx "
            #  f"ON {table} (model_name, created_on);"),
        ]

        for idx_name, create_stmt in index_definitions:
            cursor.execute(create_stmt)

        connection.commit()
        success = True

        if verbose and reportMajorDatabaseActions:
            printBoldGreen(f"Ensured existence of performance indexes on {table}")

    except Exception as e:
        printBoldRed(f"Error creating/ensuring indexes: {e}")
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

    return success


# ************************* BEGIN - MACHINE_SETTINGS TABLE ******************************************
#
# Machine 2 settings, edited in the frontend (Machine Settings menu; Administrator and Supervisor only) and written by
# the IOServer to the PLC in state 2 of every cycle. Append-only: every save adds a row, the latest row is in force.
# The same limits are enforced here, in the frontend dialog and by CHECK constraints in the table.

MACHINE_SETTINGS_LIMITS = {
    # key: (minimum, maximum, minimum inclusive, maximum inclusive, decimals shown in the frontend)
    "rotation_rpm": (20.0, 40.0, True, True, 0),
    "tightening_torque1_setpoint": (0.0, 20.0, False, False, 2),
    "tightening_torque2_setpoint": (0.0, 20.0, False, False, 2),
    "no_of_rotations_round1": (1.0, 4.0, True, True, 0),
    "no_of_rotations_round2": (1.0, 4.0, True, True, 0),
}
MACHINE_SETTINGS_KEYS = tuple(MACHINE_SETTINGS_LIMITS)
# Order of the two rounds of rotations, per side (CHANGES 234): CW.CCW = round 1 clockwise, round 2 counter-clockwise;
# CCW.CW = round 1 counter-clockwise, round 2 clockwise. Picks which round goes to PC_PLC_NoOfRotation1CW and which to
# PC_PLC_NoOfRotation1CCW (rotationCountsForSide); the tags and their addresses are the same as before.
ROTATION_ORDER_CW_FIRST = "CW.CCW"
ROTATION_ORDER_CCW_FIRST = "CCW.CW"
ROTATION_ORDERS = (ROTATION_ORDER_CW_FIRST, ROTATION_ORDER_CCW_FIRST)
MACHINE_SETTINGS_CHOICES = {"rotation_order_lhs": ROTATION_ORDERS, "rotation_order_rhs": ROTATION_ORDERS}
MACHINE_SETTINGS_CHOICE_KEYS = tuple(MACHINE_SETTINGS_CHOICES)
DEFAULT_MACHINE_SETTINGS_CHOICES = {"rotation_order_lhs": ROTATION_ORDER_CW_FIRST,
                                    "rotation_order_rhs": ROTATION_ORDER_CW_FIRST}
# On / off settings (per model, like the others; not range-checked). bypass_press_and_tightening2: skip the bung, the
# component press, the bung removal and the second tightening (written to the PLC as PC_PLC_Skip_Second_Tightening).
MACHINE_SETTINGS_FLAGS = ("bypass_press_and_tightening2",)
DEFAULT_MACHINE_SETTINGS_FLAGS = {"bypass_press_and_tightening2": False}
DEFAULT_MACHINE_SETTINGS = {"rotation_rpm": 30.0, "tightening_torque1_setpoint": 15.0,
                            "tightening_torque2_setpoint": 19.0, "no_of_rotations_round1": 1.0,
                            "no_of_rotations_round2": 1.0, **DEFAULT_MACHINE_SETTINGS_CHOICES}


def rotationCountsForSide(settings: dict, side: str) -> tuple:
    """(clockwise count, counter-clockwise count, order) to write to PC_PLC_NoOfRotation1CW / PC_PLC_NoOfRotation1CCW
    for a component of side "LHS" / "RHS", from a model's machine settings: the side's order picks which round is
    clockwise. LHS with CW.CCW, round 1 = 1, round 2 = 2 -> CW 1, CCW 2; with CCW.CW -> CW 2, CCW 1. Raises
    ValueError for an unknown side or order."""
    side = str(side or "").strip().upper()
    if side not in ("LHS", "RHS"):
        raise ValueError(f"unknown side {side!r}")
    order = str(settings.get(f"rotation_order_{side.lower()}") or "").strip().upper()
    round1, round2 = float(settings["no_of_rotations_round1"]), float(settings["no_of_rotations_round2"])
    if order == ROTATION_ORDER_CW_FIRST:
        return round1, round2, order
    if order == ROTATION_ORDER_CCW_FIRST:
        return round2, round1, order
    raise ValueError(f"unknown order of rotations {order!r} for {side}")


def validateMachineSettings(settings: dict) -> List[str]:
    """Returns a list of problems (empty = valid). Rotation RPM and the rotation counts must be whole numbers; the
    counts must be 1 to 4; the torque setpoints must have at most 2 decimals; the orders CW.CCW or CCW.CW."""
    problems = []
    for key, allowed in MACHINE_SETTINGS_CHOICES.items():
        if key not in settings or settings[key] is None:
            problems.append(f"{key}: missing")
        elif str(settings[key]).strip().upper() not in allowed:
            problems.append(f"{key}: {settings[key]!r} is not one of {', '.join(allowed)}")
    for key, (lo, hi, lo_incl, hi_incl, decimals) in MACHINE_SETTINGS_LIMITS.items():
        if key not in settings or settings[key] is None:
            problems.append(f"{key}: missing")
            continue
        try:
            v = float(settings[key])
        except (TypeError, ValueError):
            problems.append(f"{key}: {settings[key]!r} is not a number")
            continue
        if (v < lo or (v == lo and not lo_incl)) or (v > hi or (v == hi and not hi_incl)):
            problems.append(f"{key}: {v:g} outside {'[' if lo_incl else '('}{lo:g}, {hi:g}{']' if hi_incl else ')'}")
        elif round(v, decimals) != v:
            problems.append(f"{key}: {v} has more than {decimals} decimal(s)")
    return problems


def _settingsConnection(db_name: str):
    aConnection = psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1", port="5432",
                                   database=db_name)
    aConnection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    return aConnection


def settingsModelNames() -> List[str]:
    """The models that have machine settings: the models of qrcode.partmapping.N (e.g. ["DOST", "DOSTPLUS"])."""
    try:
        from Configuration import CosThetaConfigurator
        return CosThetaConfigurator.getModelNames()
    except Exception:
        noteSuppressed()
        return []


def insertDefaultMachineSettings(db_name: str) -> bool:
    """Give every model (settingsModelNames) a settings row if it has none: a copy of the latest row without a model
    (the one set for all models used before 2026-11-08), else the defaults (RPM 30, torque setpoints 15.0 / 19.0,
    rotations 1 / 1, both orders CW.CCW). Called from setDatabaseName()."""
    global schema_name
    aConnection = None
    try:
        aConnection = _settingsConnection(db_name)
        aCursor = aConnection.cursor()
        aCursor.execute(f"SELECT {', '.join(MACHINE_SETTINGS_KEYS + MACHINE_SETTINGS_CHOICE_KEYS)} "
                        f"FROM {schema_name}.machine_settings "
                        f"WHERE model_name IS NULL ORDER BY setting_id DESC LIMIT 1;")
        legacy = aCursor.fetchone()
        flags = dict(DEFAULT_MACHINE_SETTINGS_FLAGS)          # a new model: no bypass
        if legacy is not None:
            values = dict(zip(MACHINE_SETTINGS_KEYS, (float(v) for v in legacy[:len(MACHINE_SETTINGS_KEYS)])))
            values.update(zip(MACHINE_SETTINGS_CHOICE_KEYS, legacy[len(MACHINE_SETTINGS_KEYS):]))
            for k in ("no_of_rotations_round1", "no_of_rotations_round2"):  # stored before the 1 - 4 range: keep in range
                values[k] = min(4.0, max(1.0, values[k]))
            source = "system (copied from the settings for all models)"
        else:
            values, source = dict(DEFAULT_MACHINE_SETTINGS), "system (default)"
        models = settingsModelNames() or [None]
        for model in models:
            if model is None:
                aCursor.execute(f"SELECT COUNT(*) FROM {schema_name}.machine_settings;")
            else:
                aCursor.execute(f"SELECT COUNT(*) FROM {schema_name}.machine_settings WHERE model_name = %s;", (model,))
            if aCursor.fetchone()[0] == 0:
                aCursor.execute(f"""INSERT INTO {schema_name}.machine_settings
                                        (rotation_rpm, tightening_torque1_setpoint, tightening_torque2_setpoint,
                                         no_of_rotations_round1, no_of_rotations_round2,
                                         rotation_order_lhs, rotation_order_rhs, bypass_press_and_tightening2,
                                         model_name, updated_by)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);""",
                                tuple(values[k] for k in MACHINE_SETTINGS_KEYS)
                                + tuple(values[k] for k in MACHINE_SETTINGS_CHOICE_KEYS)
                                + tuple(bool(flags[k]) for k in MACHINE_SETTINGS_FLAGS) + (model, source))
                if reportMajorDatabaseActions:
                    printBoldGreen(f"Machine settings for {model or 'all models'} in {schema_name}: {source}")
        aCursor.close()
        aConnection.close()
        return True
    except Exception as error:
        printBoldRed(f"Error inserting default machine settings: {error}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
        return False


_SETTINGS_COLUMNS = "setting_id, rotation_rpm, tightening_torque1_setpoint, tightening_torque2_setpoint, " \
                    "no_of_rotations_round1, no_of_rotations_round2, updated_by, updated_on_date, model_name, " \
                    "bypass_press_and_tightening2, rotation_order_lhs, rotation_order_rhs"


def _settingsRowToDict(row) -> dict:
    keys = ("setting_id",) + MACHINE_SETTINGS_KEYS + ("updated_by", "updated_on_date", "model_name") \
           + MACHINE_SETTINGS_FLAGS + MACHINE_SETTINGS_CHOICE_KEYS
    d = dict(zip(keys, row))
    for k in MACHINE_SETTINGS_KEYS:
        d[k] = float(d[k])
    for k in MACHINE_SETTINGS_FLAGS:
        d[k] = bool(d[k])
    for k in MACHINE_SETTINGS_CHOICE_KEYS:
        d[k] = str(d[k]).strip().upper()
    return d


def getMachineSettings(db_name: str, model_name: Optional[str] = None) -> dict:
    """The settings in force for model_name (its latest row; if it has none, the latest row without a model) as a
    dict with keys rotation_rpm, tightening_torque1_setpoint, tightening_torque2_setpoint, no_of_rotations_round1,
    no_of_rotations_round2 (floats), rotation_order_lhs, rotation_order_rhs (CW.CCW / CCW.CW),
    bypass_press_and_tightening2, setting_id, updated_by, updated_on_date, model_name. model_name None: the latest row
    of any model. Empty dict on error / no row."""
    global schema_name
    aConnection = None
    try:
        aConnection = _settingsConnection(db_name)
        aCursor = aConnection.cursor()
        row = None
        if model_name:
            aCursor.execute(f"SELECT {_SETTINGS_COLUMNS} FROM {schema_name}.machine_settings WHERE model_name = %s "
                            f"ORDER BY setting_id DESC LIMIT 1;", (str(model_name).strip().upper(),))
            row = aCursor.fetchone()
            if row is None:
                aCursor.execute(f"SELECT {_SETTINGS_COLUMNS} FROM {schema_name}.machine_settings "
                                f"WHERE model_name IS NULL ORDER BY setting_id DESC LIMIT 1;")
                row = aCursor.fetchone()
        else:
            aCursor.execute(f"SELECT {_SETTINGS_COLUMNS} FROM {schema_name}.machine_settings "
                            f"ORDER BY setting_id DESC LIMIT 1;")
            row = aCursor.fetchone()
        aCursor.close()
        aConnection.close()
        return _settingsRowToDict(row) if row else {}
    except Exception as error:
        printBoldRed(f"Error fetching machine settings: {error}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
        return {}


def insertNewMachineSettings(db_name: str, rotation_rpm: float, tightening_torque1_setpoint: float,
                             tightening_torque2_setpoint: float, no_of_rotations_round1: float,
                             no_of_rotations_round2: float, rotation_order_lhs: str = ROTATION_ORDER_CW_FIRST,
                             rotation_order_rhs: str = ROTATION_ORDER_CW_FIRST, updated_by: str = "unknown",
                             model_name: Optional[str] = None,
                             bypass_press_and_tightening2: bool = False) -> bool:
    """Save new settings for model_name (in force for it from its next component); model_name None: for every model
    (settingsModelNames). Validated first; returns False and prints the problems if any value is out of range."""
    global schema_name
    values = {"rotation_rpm": rotation_rpm, "tightening_torque1_setpoint": tightening_torque1_setpoint,
              "tightening_torque2_setpoint": tightening_torque2_setpoint,
              "no_of_rotations_round1": no_of_rotations_round1, "no_of_rotations_round2": no_of_rotations_round2,
              "rotation_order_lhs": rotation_order_lhs, "rotation_order_rhs": rotation_order_rhs}
    problems = validateMachineSettings(values)
    if problems:
        printBoldRed(f"Machine settings not saved: {'; '.join(problems)}")
        return False
    models = [str(model_name).strip().upper()] if model_name else (settingsModelNames() or [None])
    aConnection = None
    try:
        aConnection = _settingsConnection(db_name)
        aCursor = aConnection.cursor()
        for model in models:
            aCursor.execute(f"""INSERT INTO {schema_name}.machine_settings
                                    (rotation_rpm, tightening_torque1_setpoint, tightening_torque2_setpoint,
                                     no_of_rotations_round1, no_of_rotations_round2,
                                     rotation_order_lhs, rotation_order_rhs, bypass_press_and_tightening2,
                                     model_name, updated_by)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);""",
                            tuple(float(values[k]) for k in MACHINE_SETTINGS_KEYS)
                            + tuple(str(values[k]).strip().upper() for k in MACHINE_SETTINGS_CHOICE_KEYS)
                            + (bool(bypass_press_and_tightening2), model, str(updated_by)[:100]))
        aCursor.close()
        aConnection.close()
        if reportMajorDatabaseActions:
            printBoldGreen(f"Saved new machine settings for {', '.join(m or 'all models' for m in models)} "
                           f"({updated_by}): {values}")
        return True
    except Exception as error:
        printBoldRed(f"Error saving machine settings: {error}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
        return False


def getMachineSettingsHistory(db_name: str, limit: int = 10, model_name: Optional[str] = None) -> list:
    """The latest `limit` settings rows (of model_name, if given), newest first (same dict layout as
    getMachineSettings)."""
    global schema_name
    aConnection = None
    try:
        aConnection = _settingsConnection(db_name)
        aCursor = aConnection.cursor()
        if model_name:
            aCursor.execute(f"SELECT {_SETTINGS_COLUMNS} FROM {schema_name}.machine_settings WHERE model_name = %s "
                            f"ORDER BY setting_id DESC LIMIT %s;", (str(model_name).strip().upper(), limit))
        else:
            aCursor.execute(f"SELECT {_SETTINGS_COLUMNS} FROM {schema_name}.machine_settings "
                            f"ORDER BY setting_id DESC LIMIT %s;", (limit,))
        rows = aCursor.fetchall()
        aCursor.close()
        aConnection.close()
        return [_settingsRowToDict(r) for r in rows]
    except Exception as error:
        printBoldRed(f"Error fetching machine settings history: {error}")
        if aConnection is not None:
            try:
                aConnection.close()
            except Exception:
                noteSuppressed()
        return []

# ************************* END - MACHINE_SETTINGS TABLE ******************************************
