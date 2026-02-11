import enum

class ProductLineEnum(str, enum.Enum):
    """Product line categories"""
    ASSEMBLY = "ASSEMBLY"
    BRUSH = "BRUSH"
    CHOKE = "CHOKE"
    SEAL = "SEAL"
    FRICTION = "FRICTION"


class PlantEnum(str, enum.Enum):
    """Manufacturing plant locations"""
    MONTERREY = "MONTERREY"
    KUNSHAN = "KUNSHAN"
    CHENNAI = "CHENNAI"
    DAUGU = "DAUGU"
    TIANJIN = "TIANJIN"
    POITIERS = "POITIERS"
    FRANKFURT = "FRANKFURT"
    SCEET = "SCEET"
    SAME = "SAME"
    AMIENS = "AMIENS"
    ANHUI = "ANHUI"
    KOREA = "KOREA"
