"""Environment validation for SolidWorks MCP Server."""

import platform
import shutil

from loguru import logger

from ..config import SolidWorksMCPConfig
from ..exceptions import SolidWorksMCPError


async def validate_environment(config: SolidWorksMCPConfig) -> None:
    """Validate runtime prerequisites for the server.

    Args:
        config (SolidWorksMCPConfig): Configuration values for the operation.

    Returns:
        None: None.

    Raises:
        SolidWorksMCPError: If the operation cannot be completed.
    """
    logger.info("Validating environment...")

    # Check platform
    if not config.can_use_solidworks and not config.mock_solidworks:
        if platform.system() != "Windows":
            logger.warning("SolidWorks requires Windows platform. Using mock adapter.")
        else:
            logger.warning("SolidWorks not available. Using mock adapter.")

    # Check SolidWorks installation if on Windows
    if (
        config.enable_windows_validation
        and platform.system() == "Windows"
        and not config.mock_solidworks
    ):
        await _validate_solidworks_installation(config)

    # Check Python version
    import sys

    python_version = sys.version_info
    if python_version < (3, 11):
        raise SolidWorksMCPError(
            f"Python 3.11+ required, but running {python_version.major}.{python_version.minor}"
        )

    logger.info("Environment validation complete")


async def _validate_solidworks_installation(config: SolidWorksMCPConfig) -> None:
    """Validate SolidWorks availability on Windows hosts.

    Args:
        config (SolidWorksMCPConfig): Configuration values for the operation.

    Returns:
        None: None.
    """
    # Check if SolidWorks executable exists
    if config.solidworks_path:
        if not shutil.which(config.solidworks_path):
            logger.warning(
                f"SolidWorks executable not found at: {config.solidworks_path}"
            )

    # Attach-only probe: Dispatch would COM-start SolidWorks and can show
    # different startup dialogs than an interactive desktop launch.
    try:
        import win32com.client

        win32com.client.GetActiveObject("SldWorks.Application")
        logger.info("A running SolidWorks COM interface is available")
    except Exception as e:
        logger.warning(
            "No running SolidWorks COM interface is available. Start SolidWorks "
            f"from the desktop shortcut before connecting: {e}"
        )
