from pydantic import BaseModel


class InfrastructureSpec(BaseModel):
    name: str