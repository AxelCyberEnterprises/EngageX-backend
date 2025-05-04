# EngageX Backend

A Django-based backend for the EngageX application, providing functionality for user management, payment processing, and
practice session tracking.

## Overview

EngageX is an AI-powered application that helps users improve their speaking, presentation, and pitch skills. The
backend is built with Django and Django REST Framework (DRF) and includes features such as:

- **User Management:** Registration, email verification, authentication (including third-party logins), password resets,
  and profile updates.
- **Payment Processing:** Integration with Intuit’s payment gateway to convert payments into session credits.
- **Practice Sessions:** Endpoints for managing practice session history, detailed session reports, and dashboard
  analytics for both users and admins.

## Features

- **User Authentication:** Registration with email verification, login with token authentication, and social login (
  e.g., Google OAUTH2).
- **Password Management:** Password reset via OTP and password change functionality.
- **Payment Integration:** Handling of tier-based payment transactions and conversion of payments into session credits.
- **Session Management:** CRUD endpoints for practice sessions, detailed session reports, and aggregated dashboard
  statistics.
- **Role-Based Access Control:** Different functionalities available for regular users vs. admin users.

## Technologies

- **Backend Framework:** Django 3/4 (Django REST Framework)
- **Database:** PostgreSQL (with AWS RDS in production)
- **Authentication:** djoser, DRF Token Authentication, and optionally OAuth2 (Google)
- **Documentation:** Swagger/OpenAPI via drf-yasg

## Requirements

- Python 3.8+
- Django 3.2+ (or Django 4.x)
- Django REST Framework
- psycopg2-binary (for PostgreSQL)
- drf-yasg (for API documentation)
- Other dependencies as listed in `requirements.txt`

## Installation

1. **Clone the Repository:**
   git clone https://github.com/AxelCyberEnterprises/EngageX-backend.git

2. Create a Virtual Environment:
   python -m venv venv
   source venv/bin/activate # On Windows: venv/Scripts/activate

3. Install Dependencies:
   pip install -r requirements.txt

4. Configure Environment Variables: Create a .env file in the project root (the same directory as manage.py) with
   content similar to:
   #PostgreSQL Config
   POSTGRESQL_DATABASE_NAME=database1
   <<<<<<< HEAD
   POSTGRESQL_USERNAME=postgres
   POSTGRESQL_PASSWORD=Engage_x001
   POSTGRESQL_SERVER_NAME=engage-x-db-backup.czamkg8gifje.us-west-1.rds.amazonaws.com
   PORT=5432
   =======
   export POSTGRESQL_USERNAME=postgres
   export POSTGRESQL_PASSWORD=Engage_x001
   export POSTGRESQL_SERVER_NAME=engage-x-db-backup.czamkg8gifje.us-west-1.rds.amazonaws.com
   export PORT=5432

> > > > > > > deploy

5. Run Migrations:
   python manage.py makemigrations
   python manage.py migrate

6. Create a Superuser:
   python manage.py createsuperuser

7. Run the Development Server:
   python manage.py runserver

API Documentation
Swagger/OpenAPI documentation is auto-generated using drf-yasg.

Swagger UI: http://localhost:8000/swagger/
ReDoc: http://localhost:8000/redoc/

Running Tests
To run all automated tests, execute:
python manage.py test

Security
Set DEBUG=False in production.
Use a secure SECRET_KEY.
Configure ALLOWED_HOSTS appropriately.
Ensure proper SSL configuration.

Project Structure

engagex-backend/
├── payments/ # Payment processing app
│ ├── migrations/
│ ├── models.py
│ ├── serializers.py
│ ├── tests.py
│ ├── urls.py
│ └── views.py
├── practice_sessions/ # Practice session management app
│ ├── migrations/
│ ├── models.py
│ ├── serializers.py
│ ├── tests.py
│ ├── urls.py
│ └── views.py
├── users/ # Base user functionality
│ ├── migrations/
│ ├── models.py
│ ├── serializers.py
│ ├── tests.py
│ ├── urls.py
│ └── views.py
├── manage.py
├── requirements.txt
└── README.md

Contributing
Contributions are welcome! Please follow these steps:

Fork the repository.
Create a new branch for your feature or bugfix.
Submit a pull request with a clear description of your changes.
Ensure all tests pass before submitting.
