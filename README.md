# Problem Solving Project - Backend

FastAPI-based backend application with PostgreSQL database.

## Prerequisites

- Python 3.11+
- PostgreSQL 12+
- pip

## Setup

### 1. Clone the Repository

```bash
git clone https://github.com/STS-Engineer/problem-solving-app-backend.git
cd problem-solving-project-backend
```

### 2. Create Virtual Environment

```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment
# On Windows:
.venv\Scripts\activate

# On macOS/Linux:
source .venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Environment Configuration

Create a `.env` file in the project root:

```env
# Database
DATABASE_URL=postgresql://username:password@host:port/database_name


```

### 5. Database Migration

```bash
# Initialize Alembic (only if not initialized)
alembic init alembic

# Generate migration
alembic revision --autogenerate -m "initial migration"

# Apply migration
alembic upgrade head
```

### 6. Run the Application

```bash
# Development server
uvicorn app.main:app --reload

# Production server
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Project Structure

```
problem-solving-project-backend/
├── alembic/                # Database migrations
│   ├── versions/           # Migration files
│   └── env.py             # Alembic configuration
├── app/
│   ├── api/               # API routes
│   │   ├── deps.py        # Dependencies
│   │   └── routes/        # Route modules
│   ├── models/            # SQLAlchemy models
│   ├── schemas/           # Pydantic schemas
│   ├── services/          # Business logic
│   ├── db/                # Database configuration
│   │   ├── base.py        # Base model
│   │   └── session.py     # DB session
│   └── main.py            # FastAPI application
├── .env                   # Environment variables
├── alembic.ini            # Alembic configuration
└── requirements.txt       # Python dependencies
```

## Database Management

### Create New Migration

```bash
alembic revision --autogenerate -m "description of changes"
```

### Apply Migrations

```bash
# Upgrade to latest
alembic upgrade head

# Upgrade to specific revision
alembic upgrade <revision_id>
```

### Rollback Migration

```bash
# Downgrade one revision
alembic downgrade -1

# Downgrade to specific revision
alembic downgrade <revision_id>
```

### View Migration History

```bash
# Show current version
alembic current

# Show migration history
alembic history
```

## Common Commands

```bash
# Install new package
pip install <package-name>
pip freeze > requirements.txt

# Update dependencies
pip install -r requirements.txt --upgrade

# Check code style (if configured)
flake8 app/
black app/
isort app/
```

## API Documentation

Once the server is running, access:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## Troubleshooting

### Virtual Environment Not Activating

- **Windows**: Run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`
- **macOS/Linux**: Ensure script has execute permissions

### Database Connection Issues

- Verify PostgreSQL is running
- Check credentials in `.env`
- Ensure database exists: `createdb database_name`
- For Azure: Verify SSL requirements

### Migration Errors

```bash
# If migration fails, rollback and retry
alembic downgrade -1
alembic upgrade head
```

### Port Already in Use

```bash
# Use different port
uvicorn app.main:app --reload --port 8001
```

## Development Workflow

1. **Create feature branch**

   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make changes**
   - Update models
   - Create/update schemas
   - Implement services
   - Add routes

3. **Create migration**

   ```bash
   alembic revision --autogenerate -m "add feature X"
   alembic upgrade head
   ```

4. **Test changes**

   ```bash
   # Run development server
   uvicorn app.main:app --reload

   # Test API endpoints via Swagger UI
   # http://localhost:8000/docs
   ```

5. **Commit and push**
   ```bash
   git add .
   git commit -m "feat: add feature X"
   git push origin feature/your-feature-name
   ```

## Additional Resources

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [SQLAlchemy Documentation](https://docs.sqlalchemy.org/)
- [Alembic Documentation](https://alembic.sqlalchemy.org/)
- [Pydantic Documentation](https://docs.pydantic.dev/)
