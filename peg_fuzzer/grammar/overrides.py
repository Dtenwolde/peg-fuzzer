"""Rule overrides -- mirrors AddRuleOverride calls in matcher.cpp."""

from enum import Enum, auto


class OverrideKind(Enum):
    VARIABLE = auto()
    RESERVED_VARIABLE = auto()
    CATALOG_NAME = auto()
    SCHEMA_NAME = auto()
    RESERVED_SCHEMA_NAME = auto()
    TABLE_NAME = auto()
    RESERVED_TABLE_NAME = auto()
    COLUMN_NAME = auto()
    RESERVED_COLUMN_NAME = auto()
    SCALAR_FUNCTION_NAME = auto()
    RESERVED_SCALAR_FUNCTION_NAME = auto()
    TABLE_FUNCTION_NAME = auto()
    TYPE_NAME = auto()
    PRAGMA_NAME = auto()
    SETTING_NAME = auto()
    NUMBER_LITERAL = auto()
    STRING_LITERAL = auto()
    OPERATOR_LITERAL = auto()


# Maps grammar rule names to override kinds.
# Mirrors the AddRuleOverride calls in MatcherFactory::CreateMatcher (matcher.cpp ~line 1380).
OVERRIDES: dict[str, OverrideKind] = {
    "Identifier": OverrideKind.VARIABLE,
    "ReservedIdentifier": OverrideKind.RESERVED_VARIABLE,
    "CatalogName": OverrideKind.CATALOG_NAME,
    "SchemaName": OverrideKind.SCHEMA_NAME,
    "ReservedSchemaName": OverrideKind.RESERVED_SCHEMA_NAME,
    "TableName": OverrideKind.TABLE_NAME,
    "ReservedTableName": OverrideKind.RESERVED_TABLE_NAME,
    "ColumnName": OverrideKind.COLUMN_NAME,
    "ReservedColumnName": OverrideKind.RESERVED_COLUMN_NAME,
    "IndexName": OverrideKind.VARIABLE,
    "SequenceName": OverrideKind.VARIABLE,
    "FunctionName": OverrideKind.SCALAR_FUNCTION_NAME,
    "ReservedFunctionName": OverrideKind.RESERVED_SCALAR_FUNCTION_NAME,
    "TableFunctionName": OverrideKind.TABLE_FUNCTION_NAME,
    "TypeName": OverrideKind.TYPE_NAME,
    "PragmaName": OverrideKind.PRAGMA_NAME,
    "SettingName": OverrideKind.SETTING_NAME,
    "CopyOptionName": OverrideKind.RESERVED_VARIABLE,
    "NumberLiteral": OverrideKind.NUMBER_LITERAL,
    "StringLiteral": OverrideKind.STRING_LITERAL,
    "OperatorLiteral": OverrideKind.OPERATOR_LITERAL,
    # PlainIdentifier uses a REGEX token which the generator cannot expand;
    # treat it as a plain variable name.
    "PlainIdentifier": OverrideKind.VARIABLE,
    "Parameter": OverrideKind.NUMBER_LITERAL,  # $1 style -- generate a plain number
}
