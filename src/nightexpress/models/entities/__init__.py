"""Pure domain entities (dataclasses, no Qt)."""

from nightexpress.models.entities.board import (
    BoardDefinition,
    ButtonInfo,
    I2cInfo,
    LedInfo,
    SupportStatus,
    UartInfo,
    UploadMethod,
    UploadMethodSupport,
)
from nightexpress.models.entities.chip import (
    BootloaderInfo,
    ChipDefinition,
    ChipPins,
    I2cBusPins,
    I2cPinChoice,
    chip_model_from_marking,
)
from nightexpress.models.entities.diagnostic import (
    BuildSummary,
    Diagnostic,
    DiagnosticLevel,
    MemoryUsage,
)
from nightexpress.models.entities.example import ExampleDefinition
from nightexpress.models.entities.gpio import is_valid_pin
from nightexpress.models.entities.operation import OperationState
from nightexpress.models.entities.ports import (
    DEFAULT_BAUD_RATE,
    AdapterKind,
    DfuDeviceInfo,
    ProbeInfo,
    SerialPortInfo,
    UploadTarget,
)
from nightexpress.models.entities.project import (
    BuildProfile,
    NewProjectRequest,
    ProjectConfig,
    RecentProject,
    project_name_error,
)
from nightexpress.models.entities.toolchain import ToolStatus
from nightexpress.models.entities.upload import UploadProgress, UploadStage

__all__ = [
    "DEFAULT_BAUD_RATE",
    "AdapterKind",
    "BoardDefinition",
    "BootloaderInfo",
    "BuildProfile",
    "BuildSummary",
    "ButtonInfo",
    "ChipDefinition",
    "ChipPins",
    "DfuDeviceInfo",
    "Diagnostic",
    "DiagnosticLevel",
    "ExampleDefinition",
    "I2cBusPins",
    "I2cInfo",
    "I2cPinChoice",
    "LedInfo",
    "MemoryUsage",
    "NewProjectRequest",
    "OperationState",
    "ProbeInfo",
    "ProjectConfig",
    "RecentProject",
    "SerialPortInfo",
    "SupportStatus",
    "ToolStatus",
    "UartInfo",
    "UploadMethod",
    "UploadMethodSupport",
    "UploadProgress",
    "UploadStage",
    "UploadTarget",
    "chip_model_from_marking",
    "is_valid_pin",
    "project_name_error",
]
