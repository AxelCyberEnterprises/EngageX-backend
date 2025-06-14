# EngageX Backend Handover Document

## Table of Contents
1. [System Overview](#system-overview)
2. [Core Components](#core-components)
3. [Authentication & User Management](#authentication--user-management)
4. [Payment Processing](#payment-processing)
5. [Practice Sessions Deep Dive](#practice-sessions-deep-dive)
6. [Real-time Streaming System](#real-time-streaming-system)
7. [Data Flow & Processing](#data-flow--processing)
8. [Key Integrations](#key-integrations)
9. [Performance Considerations](#performance-considerations)
10. [Deployment & Infrastructure](#deployment--infrastructure)
11. [Known Issues & Technical Debt](#known-issues--technical-debt)
12. [Recommendations](#recommendations)

## System Overview

EngageX is a comprehensive public speaking and presentation practice platform with AI-powered feedback. The backend is built on Django 5.2 and features a hybrid architecture that combines:

- **Synchronous REST APIs** (Django REST Framework)
- **Asynchronous WebSocket** (Django Channels)
- **Background Task Processing** (Celery with Redis)
- **Media Processing Pipeline** (FFmpeg, Deepgram, OpenAI)
- **Authentication & Authorization** (Djoser, JWT, Google OAuth)
- **Payment Processing** (Stripe integration)
- **Storage** (AWS S3 for media, PostgreSQL for structured data)
- **Email Notifications** (AWS SES)

## Core Components

### 1. User Management (`users/`)

#### Key Features
- Custom user model with email-based authentication
- Email verification with AWS SES
- User profiles with roles (admin, user, coach)
- Admin-user assignment system
- Password reset and account recovery

#### Key Models
- `CustomUser`: Extends AbstractBaseUser with email as username
- `UserProfile`: Extended user information
- `UserAssignment`: Links admins to users for management

#### Key Endpoints
- `POST /auth/users/`: Register new user
- `POST /auth/token/login/`: Obtain authentication token
- `GET /auth/users/me/`: Get current user profile
- `PATCH /auth/users/me/`: Update profile

### 2. Payment Processing (`payments/`)

#### Key Features
- Credit-based system with Stripe integration
- Multiple subscription tiers
- Transaction history and receipts
- Webhook handling for payment events

#### Key Models
- `PaymentTransaction`: Records all payment activities
- `CreditPackage`: Defines available credit packages

#### Key Endpoints
- `POST /payments/checkout/`: Create Stripe checkout session
- `POST /payments/webhook/`: Handle Stripe webhooks
- `GET /payments/transactions/`: List user transactions
- `GET /payments/credits/`: Check credit balance

### 3. Practice Sessions (`practice_sessions/`)

#### Key Features
- Session recording with slides
- Performance metrics tracking
- Session sequences for progress tracking
- AI-powered feedback and analysis

#### Key Models
- `PracticeSession`: Core session model
- `SessionChunk`: Segments of session recordings
- `ChunkSentimentAnalysis`: Detailed analysis per chunk
- `PracticeSequence`: Groups related sessions

### 4. Real-time Streaming (`streaming/`)

#### Key Features
- WebSocket-based media streaming
- Chunked media processing
- Real-time transcription
- AI audience questions
- Background analysis

#### Key Components
- `LiveSessionConsumer`: Handles WebSocket communication
- `sentiment_analysis.py`: AI analysis logic
- `transcription.py`: Speech-to-text processing

## Authentication & User Management

### Registration Flow
1. User submits registration form
2. System creates inactive user with verification code
3. Verification email sent via AWS SES
4. User verifies email using the code
5. Account activated

### Authentication Methods
1. **Email/Password**: Standard email and password authentication
2. **JWT Tokens**: For API access
3. **Google OAuth**: Social authentication
4. **Session Authentication**: For web interface

### Permission System
- Role-based access control (Admin, User, Coach)
- Object-level permissions
- Custom permission classes for fine-grained control

## Payment Processing

### Subscription Tiers
1. **Tester**: 5 credits
2. **Starter**: 4 credits
3. **Growth**: 6 credits
4. **Pro**: 8 credits
5. **Ultimate**: 12 credits

### Payment Flow
1. User selects credit package
2. Frontend calls `/payments/checkout/`
3. Backend creates Stripe checkout session
4. User completes payment on Stripe
5. Webhook updates user's credits

### Webhook Security
- Stripe signature verification
- Idempotency keys for duplicate handling
- Atomic credit updates

## Practice Sessions Deep Dive

### Session Lifecycle
1. **Creation**
   - User initiates new session
   - System validates available credits
   - Creates session record
   - Sets up WebSocket connection

2. **Recording**
   - Media streaming via WebSocket
   - Chunked processing
   - Real-time transcription

3. **Analysis**
   - Sentiment analysis
   - Speech metrics calculation
   - AI feedback generation

4. **Completion**
   - Finalize analysis
   - Generate reports
   - Update user metrics

### Data Model Details

#### PracticeSession
- `user`: Owner of the session
- `session_type`: Pitch, Public Speaking, or Presentation
- `goals`: User-defined objectives
- `duration`: Length of the session
- `slides_file`: Reference to presentation slides
- `compiled_video_url`: Final video URL
- Performance metrics (volume, pitch, pace, etc.)

#### SessionChunk
- `session`: Parent session
- `start_time`/`end_time`: Position in session
- `video_file`: Chunk media reference
- `transcript`: Text content
- `chunk_number`: Order in session

#### ChunkSentimentAnalysis
- `chunk`: Reference to chunk
- `audience_emotion`: Detected emotion
- Metrics (conviction, clarity, impact, etc.)
- `general_feedback_summary`: AI-generated feedback

## Real-time Streaming System

### WebSocket Connection Flow
1. Client connects with session ID and room name
2. Server validates session and permissions
3. WebSocket connection established
4. Media chunks are streamed and processed
5. Real-time feedback is sent back

### Media Processing Pipeline
1. **Chunk Reception**
   - Receive WebM chunks via WebSocket
   - Store temporarily on disk
   - Acknowledge receipt

2. **Audio Extraction**
   - Extract audio using FFmpeg
   - Normalize levels
   - Detect silence

3. **Transcription**
   - Send to Deepgram API
   - Process results
   - Update transcript buffer

4. **Analysis**
   - Sentiment analysis (OpenAI)
   - Speech metrics calculation
   - AI question generation

### Key Features

#### 1. Chunked Processing
- Fixed-size chunks (configurable)
- Overlapping windows for smooth analysis
- Background upload to S3
- Automatic cleanup

#### 2. AI Integration
- Real-time transcription (Deepgram)
- Sentiment analysis (OpenAI)
- Audience question generation
- Performance feedback

#### 3. State Management
- Connection state tracking
- Chunk processing status
- Error recovery
- Resource cleanup

## Data Flow & Processing

### Media Processing Pipeline

```
Client → WebSocket → Chunk Buffer → Audio Extraction → Transcription → Analysis → Storage
                     ↓               ↓                   ↓
                     S3 Upload      S3 Upload          S3 Upload
```

### Analysis Pipeline

1. **Audio Processing**
   - Volume normalization
   - Noise reduction
   - Silence detection

2. **Speech Analysis**
   - Word count and pace
   - Filler word detection
   - Pitch and tone analysis

3. **Content Analysis**
   - Topic modeling
   - Key phrase extraction
   - Sentiment scoring

## Key Integrations

### 1. Deepgram
- Real-time transcription
- Speaker diarization
- Language detection

### 2. OpenAI
- Sentiment analysis
- Content summarization
- Question generation

### 3. AWS Services
- **S3**: Media storage
- **SES**: Email notifications
- **Elastic Beanstalk**: Hosting
- **RDS**: PostgreSQL database
- **CloudFront**: CDN for static files

### 4. Stripe
- Payment processing
- Subscription management
- Webhook handling

## Performance Considerations

### Bottlenecks
1. **Media Processing**
   - High CPU usage during FFmpeg operations
   - Memory usage with large files
   - Network latency for S3 uploads

2. **Concurrency**
   - WebSocket message ordering
   - Database contention
   - Rate limiting external APIs

### Optimization Strategies
- Chunked processing
- Background task offloading
- Connection pooling
- Caching frequent queries
- Database indexing

## Deployment & Infrastructure

### AWS Architecture
- **Compute**: Elastic Beanstalk (auto-scaling)
- **Database**: RDS PostgreSQL
- **Storage**: S3 + CloudFront
- **Email**: SES
- **Caching**: ElastiCache Redis
- **CDN**: CloudFront

### CI/CD Pipeline
1. **GitHub Actions**
   - Linting and testing
   - Security scanning
   - Deployment to staging/production

2. **Environment Variables**
   - Separate configs per environment
   - Secrets management
   - Feature flags

### Monitoring & Logging
- **CloudWatch**
  - Application logs
  - Metrics and dashboards
  - Alarms and notifications

- **Sentry**
  - Error tracking
  - Performance monitoring
  - Release tracking

## Known Issues & Technical Debt

### Critical
1. **Memory Leaks**
   - In long-running WebSocket connections
   - During media processing

2. **Error Handling**
   - Incomplete transaction rollback
   - Partial failure recovery

### High Priority
1. **Testing**
   - Incomplete test coverage
   - Flaky integration tests

2. **Documentation**
   - Outdated API docs
   - Missing architecture diagrams

## Recommendations

### Immediate (1-2 Weeks)
1. **Stability**
   - Fix memory leaks
   - Improve error handling
   - Add circuit breakers

2. **Monitoring**
   - Enhance logging
   - Add alerts
   - Set up dashboards

### Short-term (1 Month)
1. **Performance**
   - Optimize database queries
   - Implement caching
   - Scale horizontally

2. **Features**
   - Batch processing
   - Offline mode
   - Enhanced analytics

### Long-term (3+ Months)
1. **Architecture**
   - Microservices
   - Event-driven design
   - Multi-region deployment

2. **AI/ML**
   - Custom models
   - Advanced analytics
   - Personalization

## Support Contacts

- **Backend Lead**: [Name]
- **DevOps**: [Contact]
- **AI/ML**: [Contact]
- **Support**: [Email/Channel]

## Appendix

### Environment Variables
```
# Required
DATABASE_URL=postgres://user:pass@host:port/db
AWS_ACCESS_KEY_ID=xxx
AWS_SECRET_ACCESS_KEY=xxx
AWS_STORAGE_BUCKET_NAME=engagex-media
AWS_S3_REGION_NAME=us-east-1
STRIPE_SECRET_KEY=sk_test_xxx
STRIPE_WEBHOOK_SECRET=whsec_xxx
DEEPGRAM_API_KEY=xxx
OPENAI_API_KEY=xxx
GOOGLE_OAUTH_CLIENT_ID=xxx
GOOGLE_OAUTH_CLIENT_SECRET=xxx

# Optional
DEBUG=False
CELERY_BROKER_URL=redis://localhost:6379/0
CHANNEL_LAYERS=...
ALLOWED_HOSTS=.elasticbeanstalk.com,localhost,127.0.0.1
```

### API Documentation
- Swagger UI: `/swagger/`
- ReDoc: `/redoc/`
- Postman Collection: [Link to collection]

### Monitoring Links
- [CloudWatch Dashboard](#)
- [Sentry Dashboard](#)
- [Grafana Dashboards](#)
- [AWS Cost Explorer](#)

### Runbook
[Link to detailed runbook with procedures for common operations and incidents]

## Practice Sessions Deep Dive

### Data Model

#### Key Models

1. **PracticeSession**
   - Core model representing a single practice session
   - Contains metadata, timing, and analysis results
   - Linked to users and optional sequences

2. **SessionChunk**
   - Represents a segment of a practice session
   - Stores video/audio references and transcripts
   - Linked to parent session and analysis results

3. **ChunkSentimentAnalysis**
   - Detailed analysis of each chunk
   - Includes metrics like conviction, clarity, impact
   - Stores audience emotion detection

4. **PracticeSequence**
   - Groups related practice sessions
   - Tracks progress across multiple sessions
   - Enables comparative analysis

### Key Flows

#### 1. Session Creation & Initialization
```mermaid
sequenceDiagram
    participant C as Client
    participant V as ViewSet
    participant M as Model
    participant S as Storage
    
    C->>V: POST /api/sessions/
    V->>M: Create PracticeSession
    M-->>V: New session instance
    V->>M: Save to database
    V-->>C: 201 Created with session ID
    
    alt Upload Slides
        C->>V: PUT /api/sessions/<id>/slides/
        V->>S: Upload slides to S3
        V->>M: Update session with slide URL
    end
```

#### 2. Real-time Processing Flow
```mermaid
sequenceDiagram
    participant C as Client
    participant WS as WebSocket
    participant M as Media Processor
    participant T as Transcriber
    participant A as Analyzer
    participant S as Storage
    
    C->>WS: Connect to WebSocket
    WS->>WS: Validate session/user
    
    loop Media Streaming
        C->>WS: Send media chunk
        WS->>M: Process chunk (FFmpeg)
        M->>T: Transcribe audio (Deepgram)
        M->>A: Analyze metrics
        M->>S: Store chunk (S3)
        M->>WS: Acknowledge chunk
    end
    
    WS->>A: Finalize analysis
    A->>S: Save results
    WS->>C: Session complete
```

## Real-time Streaming System

### WebSocket Consumer

The `LiveSessionConsumer` handles all real-time communication:

1. **Connection Handling**
   - Validates session and user permissions
   - Initializes buffers and state
   - Sets up cleanup on disconnect

2. **Media Processing**
   - Receives WebM chunks from client
   - Extracts audio using FFmpeg
   - Processes chunks in parallel
   - Manages temporary files

3. **Analysis Pipeline**
   - Windowed analysis of chunks
   - Real-time transcription
   - Sentiment and metrics analysis
   - AI-powered audience questions

### Key Features

1. **Chunked Processing**
   - Processes media in fixed-size chunks
   - Overlapping windows for smooth analysis
   - Background upload to S3

2. **AI Integration**
   - Real-time transcription (Deepgram)
   - Sentiment analysis (OpenAI)
   - Audience question generation

3. **State Management**
   - Tracks chunks in progress
   - Handles disconnections gracefully
   - Recovers from failures

## Data Flow & Processing

### 1. Media Processing Pipeline

```
Client → WebSocket → Chunk Buffer → Audio Extraction → Transcription → Analysis → Storage
                     ↓               ↓                   ↓
                     S3 Upload      S3 Upload          S3 Upload
```

### 2. Analysis Pipeline

1. **Audio Processing**
   - Volume normalization
   - Noise reduction
   - Silence detection

2. **Speech Analysis**
   - Word count and pace
   - Filler word detection
   - Pitch and tone analysis

3. **Content Analysis**
   - Topic modeling
   - Key phrase extraction
   - Sentiment scoring

## Key Integrations

### 1. Deepgram
- Real-time transcription
- Speaker diarization
- Language detection

### 2. OpenAI
- Sentiment analysis
- Content summarization
- Question generation

### 3. AWS Services
- S3: Media storage
- SES: Email notifications
- Elastic Beanstalk: Hosting

## Performance Considerations

### Bottlenecks
1. **Media Processing**
   - High CPU usage during FFmpeg operations
   - Memory usage with large files
   - Network latency for S3 uploads

2. **Concurrency**
   - WebSocket message ordering
   - Database contention
   - Rate limiting external APIs

### Optimization Strategies
- Chunked processing
- Background task offloading
- Connection pooling
- Caching frequent queries

## Deployment & Scaling

### Infrastructure
- **Web Servers**: Multiple instances behind ELB
- **Database**: PostgreSQL with read replicas
- **Cache**: Redis for sessions and queues
- **Storage**: S3 with CloudFront CDN

### Monitoring
- CloudWatch metrics
- Custom logging
- Error tracking
- Performance profiling

## Known Issues & Technical Debt

### Critical
1. **Memory Leaks**
   - In long-running WebSocket connections
   - During media processing

2. **Error Handling**
   - Incomplete transaction rollback
   - Partial failure recovery

### High Priority
1. **Testing**
   - Incomplete test coverage
   - Flaky integration tests

2. **Documentation**
   - Outdated API docs
   - Missing architecture diagrams

## Recommendations

### Immediate (1-2 Weeks)
1. **Stability**
   - Fix memory leaks
   - Improve error handling
   - Add circuit breakers

2. **Monitoring**
   - Enhance logging
   - Add alerts
   - Set up dashboards

### Short-term (1 Month)
1. **Performance**
   - Optimize database queries
   - Implement caching
   - Scale horizontally

2. **Features**
   - Batch processing
   - Offline mode
   - Enhanced analytics

### Long-term (3+ Months)
1. **Architecture**
   - Microservices
   - Event-driven design
   - Multi-region deployment

2. **AI/ML**
   - Custom models
   - Advanced analytics
   - Personalization

## Support Contacts

- **Backend Lead**: [Name]
- **DevOps**: [Contact]
- **AI/ML**: [Contact]

## Appendix

### Environment Variables
```
# Required
DATABASE_URL=postgres://user:pass@host:port/db
AWS_ACCESS_KEY_ID=xxx
AWS_SECRET_ACCESS_KEY=xxx
AWS_STORAGE_BUCKET_NAME=engagex-media
DEEPGRAM_API_KEY=xxx
OPENAI_API_KEY=xxx

# Optional
DEBUG=True
CELERY_BROKER_URL=redis://localhost:6379/0
CHANNEL_LAYERS=...
```

### API Endpoints
See Swagger UI at `/swagger/` for complete documentation.

### Monitoring Links
- [CloudWatch Dashboard](#)
- [Sentry](#)
- [Grafana](#)
