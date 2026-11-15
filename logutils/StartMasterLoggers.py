"""
Entry point for starting the master logging processes.

This script should be run as a separate process to handle centralized logging
from all application components.
"""
from logutils.CentralLoggers import MasterConsoleLogger, MasterFileLogger

import sys
from Configuration import CosThetaConfigurator

CosThetaConfigurator.getInstance()


def main() -> None:
    """Start both master loggers and wait for completion."""
    mcl = MasterConsoleLogger()
    mfl = MasterFileLogger()

    # Wait for both loggers to complete
    mcl.join()
    mfl.join()
    sys.exit(0)


if __name__ == "__main__":
    main()
else:
    # When imported as a module, start loggers immediately
    mcl = MasterConsoleLogger()
    mfl = MasterFileLogger()