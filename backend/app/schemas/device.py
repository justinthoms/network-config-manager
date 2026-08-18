from pydantic import BaseModel, ConfigDict, Field

class DeviceCreate(BaseModel):
    hostname: str
    management_ip: str
    vendor: str
    model: str | None = None
    username: str
    password: str
    ssh_port: int = Field(default=22, ge=1, le=65535)
    site: str = "DEFAULT"

class DeviceUpdate(BaseModel):
    hostname: str | None = None
    management_ip: str | None = None
    vendor: str | None = None
    model: str | None = None
    username: str | None = None
    password: str | None = None
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    site: str | None = None
    enabled: bool | None = None

class DeviceOut(BaseModel):
    id: int
    hostname: str
    management_ip: str
    vendor: str
    model: str | None
    username: str
    ssh_port: int
    site: str
    enabled: bool
    model_config = ConfigDict(from_attributes=True)
