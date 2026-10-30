import psycopg2
from psycopg2 import Error
import numpy as np
from datetime import datetime, timedelta
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Database configuration
db_params = {
    "host": "127.0.0.1",
    "database": "ashok_leyland_production",
    "user": "postgres",
    "password": "postgres",
    "port": "5432"
}

# Parameters
NUM_ROWS = 20000
BATCH_SIZE = 1000
MODELS = ['Model 1', 'Model 2', 'Model 3']
DUPLICATE_PROB = 0.1  # 10% of rows are DUPLICATE
SCHEMA_NAME = 'hub_and_disc_assembly_schema'

# Torque ranges with mean and std dev for normal distribution
TORQUE_RANGES = {
    'nut_tightening_torque_1': {'min': 17.7, 'max': 18.2, 'mean': 17.95, 'std': 0.0625},
    'free_rotation_torque_1': {'min': 0.9, 'max': 2.27, 'mean': 1.585, 'std': 0.1713},
    'nut_tightening_torque_2': {'min': 14.9, 'max': 15.3, 'mean': 15.1, 'std': 0.0500},
    'free_rotation_torque_2': {'min': 0.8, 'max': 2.27, 'mean': 1.535, 'std': 0.1838},
    'nut_tightening_torque_3': {'min': 12.1, 'max': 12.4, 'mean': 12.25, 'std': 0.0375},
    'free_rotation_torque_3': {'min': 12.1, 'max': 12.4, 'mean': 12.25, 'std': 0.0375}
}

# SQL insert query (36 placeholders after DEFAULT for record_id)
INSERT_QUERY = '''INSERT INTO {}.hub_and_disc_assembly_data
    (record_id, qr_code, model_name, lhs_rhs, model_tonnage, component_manufacturing_date, 
    knuckle_check_imagefile, knuckle_check_result, hub_and_first_bearing_check_imagefile, hub_and_first_bearing_check_result, 
    second_bearing_check_imagefile, second_bearing_check_result, 
    nut_and_platewasher_check_imagefile, nut_and_platewasher_check_result, 
    nut_tightening_torque_1, nut_tightening_torque_1_result, free_rotation_torque_1, free_rotation_torque_1_result, 
    nut_tightening_torque_2, nut_tightening_torque_2_result, free_rotation_torque_2, free_rotation_torque_2_result,
    nut_tightening_torque_3, nut_tightening_torque_3_result, free_rotation_torque_3, free_rotation_torque_3_result,
    splitpin_and_washer_check_imagefile, splitpin_and_washer_check_result, 
    cap_check_imagefile, cap_check_result, bung_check_imagefile, bung_check_result, 
    cap_pressed_successfully_check_result, ok_notok_result, username, remarks, created_on)
    VALUES (
        DEFAULT,
        %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    );'''


def get_table_columns():
    try:
        with psycopg2.connect(**db_params) as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = %s
                    AND table_name = 'hub_and_disc_assembly_data'
                    ORDER BY ordinal_position
                """, (SCHEMA_NAME,))
                columns = [row[0] for row in cursor.fetchall()]
                logging.info(f"Table columns: {columns}")
                return columns
    except Error as e:
        logging.error(f"Error fetching table columns: {e}")
        raise


def generate_torque_values(mean, std, min_val, max_val, size):
    """Generate torque values with normal distribution, resampling out-of-range values."""
    values = np.random.normal(mean, std, size)
    # Resample values outside [min_val, max_val]
    out_of_range = (values < min_val) | (values > max_val)
    while np.any(out_of_range):
        values[out_of_range] = np.random.normal(mean, std, size=sum(out_of_range))
        out_of_range = (values < min_val) | (values > max_val)
    return values.round(2)


def generate_synthetic_data():
    logging.info("Generating synthetic data...")
    now = datetime.now()
    start_date = now - timedelta(days=365)

    # Generate timestamps
    time_deltas = np.random.uniform(0, 365 * 24 * 60 * 60, NUM_ROWS)  # Seconds in 365 days
    timestamps = [start_date + timedelta(seconds=delta) for delta in time_deltas]
    timestamp_strs = [ts.strftime('%d-%m-%Y %H:%M:%S') for ts in timestamps]

    # Generate QR codes (unique)
    qr_codes = [f"QR_{str(i + 1).zfill(7)}" for i in range(NUM_ROWS)]

    # Generate model names
    model_names = np.random.choice(MODELS, size=NUM_ROWS, p=[1 / 3, 1 / 3, 1 / 3])

    # Generate torque values (normal distribution with resampling)
    data = {}
    for column, params in TORQUE_RANGES.items():
        data[column] = generate_torque_values(
            params['mean'], params['std'], params['min'], params['max'], NUM_ROWS
        )
        # Log distribution statistics
        logging.info(f"{column} stats: Mean={np.mean(data[column]):.2f}, "
                     f"Std={np.std(data[column]):.2f}, "
                     f"Min={np.min(data[column]):.2f}, "
                     f"Max={np.max(data[column]):.2f}")
        # Log histogram for distribution shape
        hist, bins = np.histogram(data[column], bins=10, density=True)
        logging.info(f"{column} histogram bins: {bins.round(2).tolist()}")
        logging.info(f"{column} histogram counts (normalized): {hist.round(2).tolist()}")

    # Generate remarks
    remarks = np.random.choice(['OK', 'DUPLICATE'], size=NUM_ROWS, p=[1 - DUPLICATE_PROB, DUPLICATE_PROB])

    # Default values from DDL
    default_str = 'Not Populated'
    default_result = 'OK'
    default_user = 'admin'
    default_ok_notok = 'Not OK'

    # Combine into list of tuples (36 values)
    rows = []
    for i in range(NUM_ROWS):
        row = (
            qr_codes[i],  # qr_code
            model_names[i],  # model_name
            default_str,  # lhs_rhs
            default_str,  # model_tonnage
            timestamp_strs[i],  # component_manufacturing_date
            default_str,  # knuckle_check_imagefile
            default_result,  # knuckle_check_result
            default_str,  # hub_and_first_bearing_check_imagefile
            default_result,  # hub_and_first_bearing_check_result
            default_str,  # second_bearing_check_imagefile
            default_result,  # second_bearing_check_result
            default_str,  # nut_and_platewasher_check_imagefile
            default_result,  # nut_and_platewasher_check_result
            data['nut_tightening_torque_1'][i],  # nut_tightening_torque_1
            default_result,  # nut_tightening_torque_1_result
            data['free_rotation_torque_1'][i],  # free_rotation_torque_1
            default_result,  # free_rotation_torque_1_result
            data['nut_tightening_torque_2'][i],  # nut_tightening_torque_2
            default_result,  # nut_tightening_torque_2_result
            data['free_rotation_torque_2'][i],  # free_rotation_torque_2
            default_result,  # free_rotation_torque_2_result
            data['nut_tightening_torque_3'][i],  # nut_tightening_torque_3
            default_result,  # nut_tightening_torque_3_result
            data['free_rotation_torque_3'][i],  # free_rotation_torque_3
            default_result,  # free_rotation_torque_3_result
            default_str,  # splitpin_and_washer_check_imagefile
            default_result,  # splitpin_and_washer_check_result
            default_str,  # cap_check_imagefile
            default_result,  # cap_check_result
            default_str,  # bung_check_imagefile
            default_result,  # bung_check_result
            default_result,  # cap_pressed_successfully_check_result
            default_ok_notok,  # ok_notok_result
            default_user,  # username
            remarks[i],  # remarks
            timestamp_strs[i]  # created_on
        )
        if i == 0:  # Log first row for debugging
            logging.info(f"First row values: {row}")
            logging.info(f"First row length: {len(row)}")
        rows.append(row)

    logging.info(f"Generated {NUM_ROWS} rows of synthetic data")
    placeholder_count = INSERT_QUERY.count('%s')
    logging.info(f"Number of placeholders in INSERT_QUERY: {placeholder_count}")
    if placeholder_count != 36:
        logging.error(f"Placeholder count mismatch: expected 36, got {placeholder_count}")
        raise ValueError(f"INSERT_QUERY has {placeholder_count} placeholders, expected 36")
    return rows


def insert_data(rows):
    try:
        with psycopg2.connect(**db_params) as conn:
            with conn.cursor() as cursor:
                formatted_query = INSERT_QUERY.format(SCHEMA_NAME)
                logging.info(f"Inserting {NUM_ROWS} rows in batches of {BATCH_SIZE}")
                for i in range(0, NUM_ROWS, BATCH_SIZE):
                    batch = rows[i:i + BATCH_SIZE]
                    for idx, row in enumerate(batch):
                        if len(row) != 36:
                            logging.error(f"Row {i + idx + 1} has {len(row)} values, expected 36")
                            raise ValueError(f"Row {i + idx + 1} has incorrect number of values")
                    cursor.executemany(formatted_query, batch)
                    conn.commit()
                    logging.info(f"Inserted batch {i // BATCH_SIZE + 1} ({len(batch)} rows)")
        logging.info("All data inserted successfully")
    except Error as e:
        logging.error(f"Database error: {e}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        raise


def verify_data():
    try:
        with psycopg2.connect(**db_params) as conn:
            with conn.cursor() as cursor:
                # Count total rows
                cursor.execute(f"SELECT COUNT(*) FROM {SCHEMA_NAME}.hub_and_disc_assembly_data")
                total_rows = cursor.fetchone()[0]
                logging.info(f"Total rows in table: {total_rows}")

                # Verify DUPLICATE percentage
                cursor.execute(
                    f"SELECT COUNT(*) FROM {SCHEMA_NAME}.hub_and_disc_assembly_data WHERE remarks = 'DUPLICATE'")
                duplicate_count = cursor.fetchone()[0]
                logging.info(f"DUPLICATE rows: {duplicate_count} ({duplicate_count / total_rows * 100:.2f}%)")

                # Verify model distribution
                cursor.execute(
                    f"SELECT model_name, COUNT(*) FROM {SCHEMA_NAME}.hub_and_disc_assembly_data GROUP BY model_name")
                model_counts = cursor.fetchall()
                for model, count in model_counts:
                    logging.info(f"Model {model}: {count} rows ({count / total_rows * 100:.2f}%)")

                # Verify torque ranges and distribution
                for column, params in TORQUE_RANGES.items():
                    cursor.execute(f"SELECT MIN({column}), MAX({column}), AVG({column}), STDDEV({column}) "
                                   f"FROM {SCHEMA_NAME}.hub_and_disc_assembly_data WHERE {column} != -1.0")
                    min_val, max_val, avg_val, std_val = cursor.fetchone()
                    logging.info(f"{column}: Min={min_val:.2f}, Max={max_val:.2f}, "
                                 f"Mean={avg_val:.2f} (Expected ~{params['mean']:.2f}), "
                                 f"Std={std_val:.2f} (Expected ~{params['std']:.2f})")

                # Verify qr_code uniqueness
                cursor.execute(
                    f"SELECT COUNT(DISTINCT qr_code), COUNT(*) FROM {SCHEMA_NAME}.hub_and_disc_assembly_data")
                distinct_qr, total_qr = cursor.fetchone()
                logging.info(f"Unique QR codes: {distinct_qr} (Total rows: {total_qr})")

                # Verify created_on range
                cursor.execute(f"SELECT MIN(created_on), MAX(created_on) FROM {SCHEMA_NAME}.hub_and_disc_assembly_data")
                min_date, max_date = cursor.fetchone()
                logging.info(f"created_on: Min={min_date}, Max={max_date}")
    except Error as e:
        logging.error(f"Database error during verification: {e}")
        raise


if __name__ == "__main__":
    try:
        get_table_columns()  # Log table schema for verification
        rows = generate_synthetic_data()
        insert_data(rows)
        verify_data()
    except Exception as e:
        logging.error(f"Script failed: {e}")