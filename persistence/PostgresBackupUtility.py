# from datetime import datetime
# import subprocess
# import os
# import tempfile
# from tempfile import mkstemp
#
# import gzip
# import boto3
# import psycopg2
# from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
#
# from Configuration import CosThetaConfigurator
# from utils.CosThetaPrintUtils import *
# from utils.CosThetaFileUtils import *
#
# # Amazon S3 settings.
# # AWS_ACCESS_KEY_ID  in ~/.aws/credentials
# # AWS_SECRET_ACCESS_KEY in ~/.aws/credentials
#
#
# def upload_to_s3(file_full_path, dest_file, manager_config):
#     """
#     Upload a file to an AWS S3 bucket.
#     """
#     s3_client = boto3.client('s3')
#     try:
#         s3_client.upload_file(file_full_path,
#                               manager_config.get('AWS_BUCKET_NAME'),
#                               manager_config.get('AWS_BUCKET_PATH') + dest_file)
#         os.remove(file_full_path)
#     except Exception as exc:
#         print(exc)
#         # exit(1)
#
#
# def download_from_s3(backup_s3_key, dest_file, manager_config):
#     """
#     Upload a file to an AWS S3 bucket.
#     """
#     s3_client = boto3.resource('s3')
#     try:
#         s3_client.meta.clientForReads.download_file(manager_config.get('AWS_BUCKET_NAME'), backup_s3_key, dest_file)
#     except Exception as e:
#         print(e)
#         # exit(1)
#
#
# def list_available_backups(storage_engine, manager_config):
#     key_list = []
#     backup_folder = ''
#     backup_list = []
#     if storage_engine == 'LOCAL':
#         try:
#             backup_folder = manager_config.get('LOCAL_BACKUP_PATH')
#             backup_list = os.listdir(backup_folder)
#         except FileNotFoundError:
#             print(f'Could not find {backup_folder} when searching for backups.'
#                   f'Check your .config file settings')
#             # exit(1)
#     elif storage_engine == 'S3':
#         # logger.info('Listing S3 bucket s3://{}/{} content :'.format(aws_bucket_name, aws_bucket_path))
#         s3_client = boto3.client('s3')
#         s3_objects = s3_client.list_objects_v2(Bucket=manager_config.get('AWS_BUCKET_NAME'),
#                                                Prefix=manager_config.get('AWS_BUCKET_PATH'))
#         backup_list = [s3_content['Key'] for s3_content in s3_objects['Contents']]
#
#     for bckp in backup_list:
#         key_list.append(bckp)
#     return key_list
#
#
# def list_postgres_databases(host, database_name, port, user, password):
#     try:
#         process = subprocess.Popen(
#             ['psql',
#              '--dbname=postgresql://{}:{}@{}:{}/{}'.format(user, password, host, port, database_name),
#              '--list'],
#             stdout=subprocess.PIPE
#         )
#         output = process.communicate()[0]
#         if int(process.returncode) != 0:
#             print('Command failed. Return code : {}'.format(process.returncode))
#             # exit(1)
#         return output
#     except Exception as e:
#         print(e)
#         # exit(1)
#
#
# def backup_postgres_db(host, database_name, port, user, password, dest_file, verbose : bool = False):
#     """
#     Backup postgres db to a file.
#     """
#     if verbose:
#         try:
#             # process = subprocess.Popen(
#             #     ['pg_dump',
#             #      '--dbname=postgresql://{}:{}@{}:{}/{}'.format(user, password, host, port, database_name),
#             #      '-Fc',
#             #      '-f', dest_file,
#             #      '-v'],
#             #     stdout=subprocess.PIPE
#             # )
#             process = subprocess.Popen(
#                 ['pg_dump.exe',
#                  f'--file={dest_file}',
#                  f'--dbname=postgresql://{user}:{password}@{host}:{port}/{database_name}',
#                  '--role=postgres',
#                  '--format=t',
#                  '--blobs',
#                  '--verbose',
#                  '--create'],
#                 stdout=subprocess.PIPE
#             )
#             # print("the commandline is {}".format(process.args))
#             output = process.communicate()[0]
#             if int(process.returncode) != 0:
#                 print('Command failed. Return code : {}'.format(process.returncode))
#                 # exit(1)
#             return output
#         except Exception as e:
#             print(e)
#             # exit(1)
#     else:
#
#         try:
#             # process = subprocess.Popen(
#             #     ['pg_dump',
#             #      '--dbname=postgresql://{}:{}@{}:{}/{}'.format(user, password, host, port, database_name),
#             #      '-f', dest_file],
#             #     stdout=subprocess.PIPE
#             # )
#             process = subprocess.Popen(
#                 ['pg_dump.exe',
#                  f'--file={dest_file}',
#                  f'--dbname=postgresql://{user}:{password}@{host}:{port}/{database_name}',
#                  '--role=postgres',
#                  '--format=t',
#                  '--blobs',
#                  '--create'],
#                 stdout=subprocess.PIPE
#             )
#             # print("the commandline is {}".format(process.args))
#             output = process.communicate()[0]
#             if process.returncode != 0:
#                 print('Command failed. Return code : {}'.format(process.returncode))
#                 # exit(1)
#             return output
#         except Exception as e:
#             print(e)
#             # exit(1)
#
#
# def compress_file(src_file):
#     compressed_file = "{}.gz".format(str(src_file))
#     with open(src_file, 'rb') as f_in:
#         with gzip.open(compressed_file, 'wb') as f_out:
#             for line in f_in:
#                 f_out.write(line)
#     return compressed_file
#
#
# def extract_file(src_file):
#     extracted_file, extension = os.path.splitext(src_file)
#
#     with gzip.open(src_file, 'rb') as f_in:
#         with open(extracted_file, 'wb') as f_out:
#             for line in f_in:
#                 f_out.write(line)
#     return extracted_file
#
#
# def remove_faulty_statement_from_dump(src_file):
#     temp_file, _ = tempfile.mkstemp()
#
#     try:
#         with open(temp_file, 'w+'):
#             process = subprocess.Popen(
#                 ['pg_restore',
#                  '-l'
#                  '-v',
#                  src_file],
#                 stdout=subprocess.PIPE
#             )
#             output = subprocess.check_output(('grep', '-v', '"EXTENSION - plpgsql"'), stdin=process.stdout)
#             process.wait()
#             if int(process.returncode) != 0:
#                 print('Command failed. Return code : {}'.format(process.returncode))
#                 # exit(1)
#
#             os.remove(src_file)
#             with open(src_file, 'w+') as cleaned_dump:
#                 subprocess.call(
#                     ['pg_restore',
#                      '-L'],
#                     stdin=output,
#                     stdout=cleaned_dump
#                 )
#
#     except Exception as e:
#         print("Issue when modifying dump : {}".format(e))
#
#
# def change_user_from_dump(source_dump_path, old_user, new_user):
#     fh, abs_path = mkstemp()
#     with os.fdopen(fh, 'w') as new_file:
#         with open(source_dump_path) as old_file:
#             for line in old_file:
#                 new_file.write(line.replace(old_user, new_user))
#     # Remove original file
#     os.remove(source_dump_path)
#     # Move newwidgets file
#     shutil.move(abs_path, source_dump_path)
#
#
# def restore_postgres_db(db_host, db, port, user, password, backup_file, verbose):
#     """Restore postgres db from a file."""
#     try:
#         subprocess_params = [
#             'pg_restore',
#             '--no-owner',
#             '--dbname=postgresql://{}:{}@{}:{}/{}'.format(user,
#                                                           password,
#                                                           db_host,
#                                                           port,
#                                                           db)
#         ]
#
#         if verbose:
#             subprocess_params.append('-v')
#
#         subprocess_params.append(backup_file)
#         process = subprocess.Popen(subprocess_params, stdout=subprocess.PIPE)
#         output = process.communicate()[0]
#
#         if int(process.returncode) != 0:
#             print('Command failed. Return code : {}'.format(process.returncode))
#
#         return output
#     except Exception as e:
#         print("Issue with the db restore : {}".format(e))
#
#
# def create_db(db_host, database, db_port, user_name, user_password):
#     try:
#         con = psycopg2.connect(dbname='postgres', port=db_port,
#                                user=user_name, host=db_host,
#                                password=user_password)
#
#     except Exception as e:
#         print(e)
#         # exit(1)
#
#     con.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
#     cur = con.cursor()
#     try:
#         cur.execute("SELECT pg_terminate_backend( pid ) "
#                     "FROM pg_stat_activity "
#                     "WHERE pid <> pg_backend_pid( ) "
#                     "AND datname = '{}'".format(database))
#         cur.execute("DROP DATABASE IF EXISTS {} ;".format(database))
#     except Exception as e:
#         print(e)
#         # exit(1)
#     cur.execute("CREATE DATABASE {} ;".format(database))
#     cur.execute("GRANT ALL PRIVILEGES ON DATABASE {} TO {} ;".format(database, user_name))
#     return database
#
#
# def swap_after_restore(db_host, restore_database, new_active_database, db_port, user_name, user_password):
#     try:
#         con = psycopg2.connect(dbname='postgres', port=db_port,
#                                user=user_name, host=db_host,
#                                password=user_password)
#         con.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
#         cur = con.cursor()
#         cur.execute("SELECT pg_terminate_backend( pid ) "
#                     "FROM pg_stat_activity "
#                     "WHERE pid <> pg_backend_pid( ) "
#                     "AND datname = '{}'".format(new_active_database))
#         cur.execute("DROP DATABASE IF EXISTS {}".format(new_active_database))
#         cur.execute('ALTER DATABASE "{}" RENAME TO "{}";'.format(restore_database, new_active_database))
#     except Exception as e:
#         print(e)
#         # exit(1)
#
#
# def move_to_local_storage(comp_file, filename_compressed, manager_config):
#     """ Move compressed backup into {LOCAL_BACKUP_PATH}. """
#     backup_folder = manager_config.get('LOCAL_BACKUP_PATH')
#     try:
#         check_folder = os.listdir(backup_folder)
#     except FileNotFoundError:
#         os.mkdir(backup_folder)
#     shutil.move(comp_file, '{}{}'.format(manager_config.get('LOCAL_BACKUP_PATH'), filename_compressed))
#
#
#
# # def main():
# #     logger = logging.getLogger(__name__)
# #     logger.setLevel(logging.INFO)
# #     handler = logging.StreamHandler()
# #     formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# #     handler.setFormatter(formatter)
# #     logger.addHandler(handler)
# #     args_parser = argparse.ArgumentParser(description='Postgres database management')
# #     args_parser.add_argument("--action",
# #                              metavar="action",
# #                              choices=['list', 'list_dbs', 'restore', 'backup'],
# #                              required=True)
# #     args_parser.add_argument("--date",
# #                              metavar="YYYY-MM-dd",
# #                              help="Date to use for restore (show with --action list)")
# #     args_parser.add_argument("--dest-db",
# #                              metavar="dest_db",
# #                              default=None,
# #                              help="Name of the new restored database")
# #     args_parser.add_argument("--verbose",
# #                              default=False,
# #                              help="Verbose output")
# #     args_parser.add_argument("--configfile",
# #                              required=True,
# #                              help="Database configuration file")
# #     args = args_parser.parse_args()
# #
# #     config = configparser.ConfigParser()
# #     config.read(args.configfile)
# #
# #     postgres_host = config.get('postgresql', 'host')
# #     postgres_port = config.get('postgresql', 'port')
# #     postgres_db = config.get('postgresql', 'db')
# #     postgres_restore = "{}_restore".format(postgres_db)
# #     postgres_user = config.get('postgresql', 'user')
# #     postgres_password = config.get('postgresql', 'password')
# #     aws_bucket_name = config.get('S3', 'bucket_name')
# #     aws_bucket_path = config.get('S3', 'bucket_backup_path')
# #     storage_engine = config.get('setup', 'storage_engine')
# #     timestr = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
# #     filename = 'backup-{}-{}.dump'.format(timestr, postgres_db)
# #     filename_compressed = '{}.gz'.format(filename)
# #     restore_filename = '/tmp/restore.dump.gz'
# #     restore_uncompressed = '/tmp/restore.dump'
# #     local_storage_path = config.get('local_storage', 'path', fallback='./backups/')
# #
# #     manager_config = {
# #         'AWS_BUCKET_NAME': aws_bucket_name,
# #         'AWS_BUCKET_PATH': aws_bucket_path,
# #         'BACKUP_PATH': '/tmp/',
# #         'LOCAL_BACKUP_PATH': local_storage_path
# #     }
# #
# #     local_file_path = '{}{}'.format(manager_config.get('BACKUP_PATH'), filename)
# #
# #     # list task
# #     if args.action == "list":
# #         backup_objects = sorted(list_available_backups(storage_engine, manager_config), reverse=True)
# #         for key in backup_objects:
# #             logger.info("Key : {}".format(key))
# #     # list databases task
# #     elif args.action == "list_dbs":
# #         result = list_postgres_databases(postgres_host,
# #                                          postgres_db,
# #                                          postgres_port,
# #                                          postgres_user,
# #                                          postgres_password)
# #         for line in result.splitlines():
# #             logger.info(line)
# #     # backup task
# #     elif args.action == "backup":
# #         logger.info('Backing up {} database to {}'.format(postgres_db, local_file_path))
# #         result = backup_postgres_db(postgres_host,
# #                                     postgres_db,
# #                                     postgres_port,
# #                                     postgres_user,
# #                                     postgres_password,
# #                                     local_file_path, args.verbose)
# #         if args.verbose:
# #             for line in result.splitlines():
# #                 logger.info(line)
# #
# #         logger.info("Backup complete")
# #         logger.info("Compressing {}".format(local_file_path))
# #         comp_file = compress_file(local_file_path)
# #         if storage_engine == 'LOCAL':
# #             logger.info('Moving {} to local storage...'.format(comp_file))
# #             move_to_local_storage(comp_file, filename_compressed, manager_config)
# #             logger.info("Moved to {}{}".format(manager_config.get('LOCAL_BACKUP_PATH'), filename_compressed))
# #         elif storage_engine == 'S3':
# #             logger.info('Uploading {} to Amazon S3...'.format(comp_file))
# #             upload_to_s3(comp_file, filename_compressed, manager_config)
# #             logger.info("Uploaded to {}".format(filename_compressed))
# #     # restore task
# #     elif args.action == "restore":
# #         if not args.date:
# #             logger.warn('No date was chosen for restore. Run again with the "list" '
# #                         'action to see available restore dates')
# #         else:
# #             try:
# #                 os.remove(restore_filename)
# #             except Exception as e:
# #                 logger.info(e)
# #             all_backup_keys = list_available_backups(storage_engine, manager_config)
# #             backup_match = [s for s in all_backup_keys if args.date in s]
# #             if backup_match:
# #                 logger.info("Found the following backup : {}".format(backup_match))
# #             else:
# #                 logger.error("No match found for backups with date : {}".format(args.date))
# #                 logger.info("Available keys : {}".format([s for s in all_backup_keys]))
# #                 exit(1)
# #
# #             if storage_engine == 'LOCAL':
# #                 logger.info("Choosing {} from local storage".format(backup_match[0]))
# #                 shutil.copy('{}/{}'.format(manager_config.get('LOCAL_BACKUP_PATH'), backup_match[0]),
# #                             restore_filename)
# #                 logger.info("Fetch complete")
# #             elif storage_engine == 'S3':
# #                 logger.info("Downloading {} from S3 into : {}".format(backup_match[0], restore_filename))
# #                 download_from_s3(backup_match[0], restore_filename, manager_config)
# #                 logger.info("Download complete")
# #
# #             logger.info("Extracting {}".format(restore_filename))
# #             ext_file = extract_file(restore_filename)
# #             # cleaned_ext_file = remove_faulty_statement_from_dump(ext_file)
# #             logger.info("Extracted to : {}".format(ext_file))
# #             logger.info("Creating temp database for restore : {}".format(postgres_restore))
# #             tmp_database = create_db(postgres_host,
# #                                      postgres_restore,
# #                                      postgres_port,
# #                                      postgres_user,
# #                                      postgres_password)
# #             logger.info("Created temp database for restore : {}".format(tmp_database))
# #             logger.info("Restore starting")
# #             result = restore_postgres_db(postgres_host,
# #                                          postgres_restore,
# #                                          postgres_port,
# #                                          postgres_user,
# #                                          postgres_password,
# #                                          restore_uncompressed,
# #                                          args.verbose)
# #             if args.verbose:
# #                 for line in result.splitlines():
# #                     logger.info(line)
# #             logger.info("Restore complete")
# #             if args.dest_db is not None:
# #                 restored_db_name = args.dest_db
# #                 logger.info("Switching restored database with newwidgets one : {} > {}".format(
# #                     postgres_restore, restored_db_name
# #                 ))
# #             else:
# #                 restored_db_name = postgres_db
# #                 logger.info("Switching restored database with active one : {} > {}".format(
# #                     postgres_restore, restored_db_name
# #                 ))
# #
# #             swap_after_restore(postgres_host,
# #                                postgres_restore,
# #                                restored_db_name,
# #                                postgres_port,
# #                                postgres_user,
# #                                postgres_password)
# #             logger.info("Database restored and active.")
# #     else:
# #         logger.warn("No valid argument was given.")
# #         logger.warn(args)
#
# def doBackup():
#     postgres_host = "127.0.0.1"
#     postgres_port = "5432"
#     postgres_db = "ashok_leyland_production"
#     postgres_user = "postgres"
#     postgres_password = "postgres"
#
#     try:
#         timestr = datetime.now().strftime('%Y%m%d-%H%M%S')
#         filename = f'backup-{postgres_db}-{timestr}.sql'
#         createDirectory(CosThetaConfigurator.getInstance().getDatabaseBackupDirectory())
#         # filename_compressed = '{}.gz'.format(filename)
#
#         local_file_path = f'{CosThetaConfigurator.getInstance().getDatabaseBackupDirectory()}{filename}'
#
#         result = backup_postgres_db(postgres_host,
#                                     postgres_db,
#                                     postgres_port,
#                                     postgres_user,
#                                     postgres_password,
#                                     local_file_path)
#         # print(result)
#         printBoldBlue(f"Database {postgres_db} backup complete")
#         comp_file = compress_file(local_file_path)
#         printBoldBlue(f"Compressed {local_file_path}")
#         filesInDirectory = listFilesInDirectory(CosThetaConfigurator.getInstance().getDatabaseBackupDirectory())
#         filesInDirectory.sort()
#         nFiles = len(filesInDirectory)
#         if nFiles > 5:
#             numberOfFilesToBeRemoved = nFiles - 5
#             # for file in filesInDirectory:
#             #     if file in comp_file :
#             #         pass
#             #     else:
#             #         try:
#             #             # print(f'{CosThetaConfigurator.getInstance().getDatabaseBackupDirectory()}{file}')
#             #             os.remove(f'{CosThetaConfigurator.getInstance().getDatabaseBackupDirectory()}{file}')
#             #             # print(f"File '{CosThetaConfigurator.getInstance().getDatabaseBackupDirectory()}{file}' sent to trash successfully.")
#             #         except FileNotFoundError:
#             #             pass
#             for i in range(numberOfFilesToBeRemoved):
#                 try:
#                     # print(f'{CosThetaConfigurator.getInstance().getDatabaseBackupDirectory()}{file}')
#                     os.remove(f'{CosThetaConfigurator.getInstance().getDatabaseBackupDirectory()}{filesInDirectory[i]}')
#                     # print(f"File '{CosThetaConfigurator.getInstance().getDatabaseBackupDirectory()}{file}' sent to trash successfully.")
#                 except FileNotFoundError:
#                     pass
#
#         try:
#             os.remove(f'{local_file_path}')
#         except:
#             pass
#     except Exception as e:
#         printBoldRed(f"Database {postgres_db} backup could not be taken because of {e}")
#         pass
#
# def doRestore(filePath : str, schema_name : str = "capsule_inspection_schema"):
#     postgres_host = "127.0.0.1"
#     postgres_port = "5432"
#     postgres_db = "ashok_leyland_production"
#     postgres_user = "postgres"
#     postgres_password = "postgres"
#
#     cur = None
#     conn = None
#     try:
#         # Connect to the PostgreSQL database
#         conn = psycopg2.connect(
#             dbname=postgres_db,
#             user=postgres_user,
#             password=postgres_password,
#             host=postgres_host,
#             port=postgres_port
#         )
#
#         # Create a cursor object
#         cur = conn.cursor()
#
#         # Execute the DROP SCHEMA command
#         cur.execute(f'DROP SCHEMA {schema_name} CASCADE;')
#
#         # Commit the changes
#         conn.commit()
#
#         print(f'Schema "{schema_name}" dropped successfully.')
#
#     except Exception as e:
#         print(f'An error occurred: {e}')
#
#     finally:
#         # Close the cursor and connection
#         if cur:
#             cur.close()
#         if conn:
#             conn.close()
#
#     try:
#         unzippedFile = extract_file(src_file=filePath)
#         result = restore_postgres_db(db_host=postgres_host, db=postgres_db, port=postgres_port, user=postgres_user, password=postgres_password, backup_file=unzippedFile, verbose=True)
#         # print(result)
#         printBoldBlue(f"Database {postgres_db} restore complete")
#     except Exception as e:
#         printBoldRed(f"Database {postgres_db} restore incomplete because of {e}")
#         pass
#
#
# # if __name__ == '__main__':
# #   doBackup()
# #     doRestore("C:/Users/bagch/Downloads/backup-geltec_production-20241116-165100.sql.gz")
#

"""
PostgreSQL Backup and Restore Utility - Production Version

This module provides secure backup and restore functionality for PostgreSQL databases.

Security Features:
- SQL injection protection using parameterized queries
- Password protection via environment variables
- Validation before destructive operations
- Comprehensive error handling and logging

Safety Features:
- Backup validation before restore
- Schema verification before dropping
- Automatic cleanup of old backups
- User confirmation for destructive operations

Author: Production Ready Version
Date: 2024
"""

from datetime import datetime
import subprocess
import os
import platform
import shutil
from typing import Optional, List, Dict
import traceback

import gzip
import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

try:
    import boto3

    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False

from Configuration import CosThetaConfigurator
from utils.CosThetaPrintUtils import *
from utils.CosThetaFileUtils import *


# ============================================================================
# Configuration and Helper Functions
# ============================================================================

def get_pg_executable(command: str) -> str:
    """
    Get platform-specific PostgreSQL executable name.

    Args:
        command: Base command name ('pg_dump', 'pg_restore', 'psql')

    Returns:
        str: Platform-specific executable name
    """
    if platform.system() == 'Windows':
        return f'{command}.exe'
    return command


def get_database_config() -> Dict[str, str]:
    """
    Get database configuration from environment variables.

    Returns:
        Dict with keys: host, port, database, user, password
    """
    config = {
        'host': os.getenv('POSTGRES_HOST', '127.0.0.1'),
        'port': os.getenv('POSTGRES_PORT', '5432'),
        'database': os.getenv('POSTGRES_DB', 'ashok_leyland_production'),
        'user': os.getenv('POSTGRES_USER', 'postgres'),
        'password': os.getenv('POSTGRES_PASSWORD', 'postgres')
    }

    # Warn if using default password
    if config['password'] == 'postgres':
        printBoldYellow("WARNING: Using default password. Set POSTGRES_PASSWORD environment variable.")

    return config


def check_database_exists(config: Dict[str, str]) -> bool:
    """
    Check if the target database exists.

    Args:
        config: Database configuration dictionary

    Returns:
        bool: True if database exists, False otherwise
    """
    conn = None
    cur = None
    try:
        # Connect to the 'postgres' database (always exists) to check if target DB exists
        conn = psycopg2.connect(
            dbname='postgres',
            user=config['user'],
            password=config['password'],
            host=config['host'],
            port=config['port'],
            connect_timeout=5
        )
        cur = conn.cursor()

        # Check if database exists
        cur.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            [config['database']]
        )

        exists = cur.fetchone() is not None
        return exists

    except psycopg2.OperationalError as e:
        # Connection failed - server might be down
        error_msg = str(e).lower()
        if 'connection refused' in error_msg:
            printBoldYellow(f"WARNING: PostgreSQL server not reachable at {config['host']}:{config['port']}")
        elif 'authentication failed' in error_msg:
            printBoldYellow(f"WARNING: Authentication failed for user '{config['user']}'")
        elif 'timeout' in error_msg:
            printBoldYellow(f"WARNING: Connection to PostgreSQL timed out")
        else:
            printBoldYellow(f"WARNING: Cannot connect to PostgreSQL: {e}")
        return False

    except Exception as e:
        printBoldYellow(f"WARNING: Database check failed: {e}")
        return False

    finally:
        if cur:
            try:
                cur.close()
            except:
                pass
        if conn:
            try:
                conn.close()
            except:
                pass

# ============================================================================
# File Compression Functions
# ============================================================================

def compress_file(src_file: str) -> Optional[str]:
    """
    Compress a file using gzip.

    Args:
        src_file: Source file path

    Returns:
        Path to compressed file, or None if failed
    """
    try:
        compressed_file = f"{src_file}.gz"
        printBoldBlue(f"Compressing {os.path.basename(src_file)}...")

        with open(src_file, 'rb') as f_in:
            with gzip.open(compressed_file, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)

        if not os.path.exists(compressed_file):
            printBoldRed("ERROR: Compressed file was not created")
            return None

        original_size = os.path.getsize(src_file)
        compressed_size = os.path.getsize(compressed_file)
        ratio = (1 - compressed_size / original_size) * 100

        printBoldGreen(f"✓ Compressed to {compressed_size:,} bytes ({ratio:.1f}% reduction)")
        return compressed_file

    except Exception as e:
        printBoldRed(f"ERROR: Compression failed: {e}")
        return None


def extract_file(src_file: str) -> Optional[str]:
    """
    Extract a gzipped file.

    Args:
        src_file: Compressed source file path

    Returns:
        Path to extracted file, or None if failed
    """
    try:
        if not os.path.exists(src_file):
            printBoldRed(f"ERROR: File not found: {src_file}")
            return None

        extracted_file = src_file[:-3] if src_file.endswith('.gz') else f"{src_file}.extracted"
        printBoldBlue(f"Extracting {os.path.basename(src_file)}...")

        with gzip.open(src_file, 'rb') as f_in:
            with open(extracted_file, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)

        if not os.path.exists(extracted_file):
            printBoldRed("ERROR: Extraction failed")
            return None

        size = os.path.getsize(extracted_file)
        printBoldGreen(f"✓ Extracted to {extracted_file} ({size:,} bytes)")
        return extracted_file

    except Exception as e:
        printBoldRed(f"ERROR: Extraction failed: {e}")
        return None


# ============================================================================
# Backup Validation
# ============================================================================

def validate_backup_file(backup_file: str) -> bool:
    """
    Validate that a backup file is readable by pg_restore.

    Args:
        backup_file: Path to backup file

    Returns:
        bool: True if valid, False otherwise
    """
    try:
        if not os.path.exists(backup_file):
            printBoldRed(f"ERROR: Backup file not found: {backup_file}")
            return False

        file_size = os.path.getsize(backup_file)
        if file_size == 0:
            printBoldRed("ERROR: Backup file is empty")
            return False

        # Test if pg_restore can read the file
        process = subprocess.Popen(
            [get_pg_executable('pg_restore'), '-l', backup_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = process.communicate()

        if process.returncode != 0:
            printBoldRed("ERROR: Backup validation failed")
            printBoldRed(f"Details: {stderr.decode()}")
            return False

        printBoldGreen(f"✓ Backup validated ({file_size:,} bytes)")
        return True

    except Exception as e:
        printBoldRed(f"ERROR: Validation failed: {e}")
        return False


# ============================================================================
# Backup Functions
# ============================================================================

def backup_postgres_db(config: Dict[str, str], dest_file: str) -> bool:
    """
    Backup PostgreSQL database to a file.

    Args:
        config: Database configuration dictionary
        dest_file: Destination file path for backup

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Set password via environment variable (secure)
        env = os.environ.copy()
        env['PGPASSWORD'] = config['password']

        connection_string = (
            f"postgresql://{config['user']}@{config['host']}:{config['port']}/{config['database']}"
        )

        args = [
            get_pg_executable('pg_dump'),
            f'--file={dest_file}',
            f'--dbname={connection_string}',
            '--format=t',
            '--blobs',
            '--create'
        ]

        printBoldBlue(f"Backing up {config['database']}...")

        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        stdout, stderr = process.communicate()

        if process.returncode != 0:
            error_text = stderr.decode().strip()

            # Parse common errors and provide friendly messages
            if 'does not exist' in error_text:
                printBoldYellow(f"Database '{config['database']}' does not exist - backup skipped")
            elif 'connection refused' in error_text.lower():
                printBoldYellow(f"PostgreSQL server not running at {config['host']}:{config['port']} - backup skipped")
            elif 'authentication failed' in error_text.lower():
                printBoldYellow(f"Authentication failed for user '{config['user']}' - backup skipped")
            elif 'permission denied' in error_text.lower():
                printBoldYellow(f"Permission denied - backup skipped")
            elif 'could not connect' in error_text.lower():
                printBoldYellow(f"Could not connect to database - backup skipped")
            else:
                printBoldRed(f"ERROR: Backup failed (code {process.returncode})")
                printBoldRed(f"Details: {error_text}")

            return False

        if not os.path.exists(dest_file):
            printBoldRed("ERROR: Backup file not created")
            return False

        size = os.path.getsize(dest_file)
        printBoldGreen(f"✓ Backup created ({size:,} bytes)")

        # Validate the backup
        if not validate_backup_file(dest_file):
            return False

        return True

    except FileNotFoundError:
        printBoldYellow(f"pg_dump not found - ensure PostgreSQL client tools are installed")
        return False

    except Exception as e:
        printBoldRed(f"ERROR: Backup failed: {e}")
        traceback.print_exc()
        return False

# ============================================================================
# Restore Functions
# ============================================================================

def restore_postgres_db(config: Dict[str, str], backup_file: str) -> bool:
    """
    Restore PostgreSQL database from a backup file.

    Args:
        config: Database configuration dictionary
        backup_file: Path to backup file

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        if not os.path.exists(backup_file):
            printBoldRed(f"ERROR: Backup file not found: {backup_file}")
            return False

        # Validate before restore
        if not validate_backup_file(backup_file):
            return False

        env = os.environ.copy()
        env['PGPASSWORD'] = config['password']

        connection_string = (
            f"postgresql://{config['user']}@{config['host']}:{config['port']}/{config['database']}"
        )

        args = [
            get_pg_executable('pg_restore'),
            '--no-owner',
            f'--dbname={connection_string}',
            '-v',
            backup_file
        ]

        printBoldBlue(f"Restoring from {os.path.basename(backup_file)}...")

        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        stdout, stderr = process.communicate()

        if process.returncode != 0:
            printBoldRed(f"ERROR: Restore failed (code {process.returncode})")
            printBoldRed(f"Details: {stderr.decode()}")
            return False

        printBoldGreen("✓ Database restored")
        return True

    except Exception as e:
        printBoldRed(f"ERROR: Restore failed: {e}")
        traceback.print_exc()
        return False


def drop_schema_safe(config: Dict[str, str], schema_name: str) -> bool:
    """
    Safely drop a database schema using parameterized queries.

    Args:
        config: Database configuration dictionary
        schema_name: Name of schema to drop

    Returns:
        bool: True if successful, False otherwise
    """
    conn = None
    cur = None

    try:
        conn = psycopg2.connect(
            dbname=config['database'],
            user=config['user'],
            password=config['password'],
            host=config['host'],
            port=config['port']
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()

        # Check if schema exists
        cur.execute(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name = %s",
            [schema_name]
        )

        if not cur.fetchone():
            printBoldYellow(f"Schema '{schema_name}' does not exist")
            return True

        printBoldYellow(f"Dropping schema '{schema_name}'...")

        # Use SQL identifier to prevent SQL injection
        cur.execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
        )

        printBoldGreen(f"✓ Schema '{schema_name}' dropped")
        return True

    except Exception as e:
        printBoldRed(f"ERROR: Failed to drop schema: {e}")
        traceback.print_exc()
        return False

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# ============================================================================
# High-Level Functions
# ============================================================================

def doBackup(keep_last_n: int = 5) -> bool:
    """
    Create a backup with automatic cleanup of old backups.

    Handles failures gracefully:
    - Database doesn't exist: logs warning, returns False
    - Server unreachable: logs warning, returns False
    - Permission denied: logs warning, returns False

    Args:
        keep_last_n: Number of most recent backups to keep

    Returns:
        bool: True if successful, False otherwise
    """
    config = None
    local_file_path = None

    try:
        config = get_database_config()
        backup_dir = CosThetaConfigurator.getInstance().getDatabaseBackupDirectory()

        # Pre-check: Verify database exists before attempting backup
        if not check_database_exists(config):
            printBoldYellow(f"BACKUP SKIPPED: Database '{config['database']}' does not exist or is unreachable")
            return False

        # Create backup directory
        try:
            createDirectory(backup_dir)
        except Exception as e:
            printBoldYellow(f"BACKUP SKIPPED: Cannot create backup directory '{backup_dir}': {e}")
            return False

        timestr = datetime.now().strftime('%Y%m%d-%H%M%S')
        filename = f"backup-{config['database']}-{timestr}.sql"
        local_file_path = os.path.join(backup_dir, filename)

        printBoldBlue("=" * 70)
        printBoldBlue("DATABASE BACKUP")
        printBoldBlue(f"Database: {config['database']}")
        printBoldBlue(f"Time: {timestr}")
        printBoldBlue("=" * 70)

        # Create backup
        if not backup_postgres_db(config, local_file_path):
            # Cleanup any partial file
            if local_file_path and os.path.exists(local_file_path):
                try:
                    os.remove(local_file_path)
                except:
                    pass
            return False

        # Compress
        compressed = compress_file(local_file_path)
        if compressed:
            try:
                os.remove(local_file_path)
            except:
                pass
            final_file = compressed
        else:
            final_file = local_file_path

        # Cleanup old backups
        printBoldBlue(f"Managing backups (keeping {keep_last_n} most recent)...")
        try:
            all_backups = sorted([f for f in os.listdir(backup_dir)
                                  if f.startswith('backup-') and config['database'] in f],
                                 reverse=True)

            if len(all_backups) > keep_last_n:
                for old_file in all_backups[keep_last_n:]:
                    try:
                        os.remove(os.path.join(backup_dir, old_file))
                        printBoldBlue(f"  Removed: {old_file}")
                    except:
                        pass
        except Exception as e:
            printBoldYellow(f"Warning: Cleanup failed: {e}")

        printBoldBlue("=" * 70)
        printBoldGreen("✓ BACKUP COMPLETE")
        printBoldGreen(f"File: {os.path.basename(final_file)}")
        printBoldBlue("=" * 70)

        return True

    except KeyboardInterrupt:
        printBoldYellow("\nBackup cancelled by user")
        # Cleanup partial file
        if local_file_path and os.path.exists(local_file_path):
            try:
                os.remove(local_file_path)
            except:
                pass
        return False

    except Exception as e:
        printBoldRed(f"✗ BACKUP FAILED: {e}")
        # Don't print traceback for known error types
        if not isinstance(e,
                          (psycopg2.OperationalError, psycopg2.ProgrammingError, FileNotFoundError, PermissionError)):
            traceback.print_exc()
        # Cleanup partial file
        if local_file_path and os.path.exists(local_file_path):
            try:
                os.remove(local_file_path)
            except:
                pass
        return False


def doRestore(backup_file_path: str,
              schema_name: str = "al_hub_and_disc_assembly_schema",
              confirm: bool = True) -> bool:
    """
    Restore database schema from backup (SAFE - validates before dropping).

    Args:
        backup_file_path: Path to backup file (.sql.gz or .sql)
        schema_name: Schema to restore
        confirm: Ask for confirmation before proceeding

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        config = get_database_config()

        printBoldBlue("=" * 70)
        printBoldYellow("DATABASE RESTORE")
        printBoldYellow(f"Database: {config['database']}")
        printBoldYellow(f"Schema: {schema_name}")
        printBoldYellow(f"Backup: {os.path.basename(backup_file_path)}")
        printBoldBlue("=" * 70)

        # Confirmation
        if confirm:
            printBoldRed("WARNING: This will DROP the existing schema!")
            response = input("Type 'YES' to continue: ")
            if response != 'YES':
                printBoldYellow("Cancelled")
                return False

        # Validate file exists
        if not os.path.exists(backup_file_path):
            printBoldRed(f"ERROR: File not found: {backup_file_path}")
            return False

        printBoldGreen(f"✓ Found: {backup_file_path}")

        # Extract if compressed
        working_file = backup_file_path
        if backup_file_path.endswith('.gz'):
            working_file = extract_file(backup_file_path)
            if not working_file:
                return False

        # Validate backup BEFORE dropping schema
        if not validate_backup_file(working_file):
            if working_file != backup_file_path:
                try:
                    os.remove(working_file)
                except:
                    pass
            return False

        # Drop schema (now safe - backup validated)
        if not drop_schema_safe(config, schema_name):
            if working_file != backup_file_path:
                try:
                    os.remove(working_file)
                except:
                    pass
            return False

        # Restore
        if not restore_postgres_db(config, working_file):
            printBoldRed("✗ RESTORE FAILED - Schema dropped but restore failed!")
            if working_file != backup_file_path:
                try:
                    os.remove(working_file)
                except:
                    pass
            return False

        # Cleanup temp file
        if working_file != backup_file_path:
            try:
                os.remove(working_file)
            except:
                pass

        printBoldBlue("=" * 70)
        printBoldGreen("✓ RESTORE COMPLETE")
        printBoldBlue("=" * 70)

        return True

    except KeyboardInterrupt:
        printBoldYellow("\nCancelled by user")
        return False
    except Exception as e:
        printBoldRed(f"✗ RESTORE FAILED: {e}")
        traceback.print_exc()
        return False


# ============================================================================
# AWS S3 Functions (Optional)
# ============================================================================

def upload_to_s3(file_full_path: str, dest_file: str, manager_config: Dict[str, str]) -> bool:
    """Upload file to S3 bucket."""
    if not BOTO3_AVAILABLE:
        printBoldRed("ERROR: boto3 not available")
        return False

    try:
        s3_client = boto3.client('s3')
        bucket = manager_config.get('AWS_BUCKET_NAME')
        key = manager_config.get('AWS_BUCKET_PATH', '') + dest_file

        s3_client.upload_file(file_full_path, bucket, key)
        os.remove(file_full_path)
        printBoldGreen(f"✓ Uploaded to S3: {key}")
        return True
    except Exception as e:
        printBoldRed(f"ERROR: S3 upload failed: {e}")
        return False


def download_from_s3(backup_s3_key: str, dest_file: str, manager_config: Dict[str, str]) -> bool:
    """Download file from S3 bucket."""
    if not BOTO3_AVAILABLE:
        printBoldRed("ERROR: boto3 not available")
        return False

    try:
        s3_client = boto3.resource('s3')
        bucket = manager_config.get('AWS_BUCKET_NAME')

        s3_client.meta.client.download_file(bucket, backup_s3_key, dest_file)
        printBoldGreen(f"✓ Downloaded from S3: {backup_s3_key}")
        return True
    except Exception as e:
        printBoldRed(f"ERROR: S3 download failed: {e}")
        return False


def list_available_backups(storage_engine: str, manager_config: Dict[str, str]) -> List[str]:
    """List backups from local or S3 storage."""
    if storage_engine == 'LOCAL':
        try:
            folder = manager_config.get('LOCAL_BACKUP_PATH')
            if not folder or not os.path.exists(folder):
                return []
            return sorted(os.listdir(folder), reverse=True)
        except:
            return []

    elif storage_engine == 'S3' and BOTO3_AVAILABLE:
        try:
            s3 = boto3.client('s3')
            bucket = manager_config.get('AWS_BUCKET_NAME')
            prefix = manager_config.get('AWS_BUCKET_PATH', '')

            response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            if 'Contents' in response:
                return sorted([obj['Key'] for obj in response['Contents']], reverse=True)
        except:
            return []

    return []


# ============================================================================
# Main Entry Point
# ============================================================================

# if __name__ == '__main__':
#     import sys
#
#     if len(sys.argv) < 2:
#         print("Usage:")
#         print("  Backup:  python PostgresBackupUtility.py backup")
#         print("  Restore: python PostgresBackupUtility.py restore <backup-file>")
#         sys.exit(1)
#
#     command = sys.argv[1].lower()
#
#     if command == 'backup':
#         success = doBackup()
#         sys.exit(0 if success else 1)
#
#     elif command == 'restore':
#         if len(sys.argv) < 3:
#             printBoldRed("ERROR: Backup file required")
#             sys.exit(1)
#
#         success = doRestore(sys.argv[2], confirm=True)
#         sys.exit(0 if success else 1)
#
#     else:
#         printBoldRed(f"ERROR: Unknown command: {command}")
#         sys.exit(1)