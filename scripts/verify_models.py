from app.core.database import Base
from app.core.models import Brand, PolymerType, Buyer, Seller, ProductOffer, Order

print("Imports successful!")
print(f"Brand table: {Brand.__tablename__}")
print(f"PolymerType table: {PolymerType.__tablename__}")
print(f"Buyer table: {Buyer.__tablename__}")
print(f"Seller table: {Seller.__tablename__}")
print(f"ProductOffer table: {ProductOffer.__tablename__}")
print(f"Order table: {Order.__tablename__}")
