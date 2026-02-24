from sqlalchemy.orm import Session
from app.core.models import PolymerType

class ProductValidator:
    @staticmethod
    def validate_polymer(polymer: str, db: Session) -> bool:
        if not polymer: return True
        # Check if polymer exists in PolymerType (grades table)
        # We check by name or code. The table has 'name' like 'PVC', 'HDPE'.
        exists = db.query(PolymerType).filter(PolymerType.name == polymer.upper()).first()
        return exists is not None
        
    @staticmethod
    def get_supported_list(db: Session) -> str:
        polymers = db.query(PolymerType).filter(PolymerType.status == '1').all()
        names = [p.name for p in polymers]
        return ", ".join(names)
