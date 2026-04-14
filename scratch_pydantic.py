from pydantic import BaseModel, ConfigDict
class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    has_pin: bool = False

class User:
    @property
    def has_pin(self):
        return True
    
    @property
    def pin_hash(self):
        return None

    @property
    def has_pin2(self):
        return self.pin_hash is not None

class UserResponse2(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    has_pin: bool = False

print(UserResponse.model_validate(User()).model_dump())

user = User()
print("has_pin2 on instance:", user.has_pin2)
