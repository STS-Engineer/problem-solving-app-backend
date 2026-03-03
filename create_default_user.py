
from app.db.session import SessionLocal

from app.models.user import User


db = SessionLocal()
user = User(
       username="admin",
       email="admin@avocarbon.com",
       password_hash="$argon2id$v=19$m=65536,t=3,p=4$mLOWcm4tJUSI0XrvfU8pZQ$I4dEKOamedFP57A/HKKqqlf+lOjFss8asV3lr602kt8",  # Use proper hashing
       role="admin",
       first_name="Admin",
       last_name="User"
   )
db.add(user)
db.commit()
print(f"Created user with ID: {user.id}")