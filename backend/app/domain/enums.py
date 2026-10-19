from enum import StrEnum


class Provider(StrEnum):
    AWS = "aws"
    AZURE = "azure"


class Region(StrEnum):
    # Which regions are allowed for which provider is checked by the
    # semantic validator (E03), not here.
    US_EAST_1 = "us-east-1"
    AP_SOUTH_1 = "ap-south-1"
    EASTUS = "eastus"
    CENTRALINDIA = "centralindia"


class Environment(StrEnum):
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class InstanceSize(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class OperatingSystem(StrEnum):
    UBUNTU = "ubuntu"


class Protocol(StrEnum):
    TCP = "tcp"
    UDP = "udp"
    ICMP = "icmp"
    ALL = "all"


class Direction(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
