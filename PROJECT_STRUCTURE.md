# AURA Project Structure - Cleaned & Optimized

## Overview
AURA (Advanced Unified Research Analytics) is a data analysis platform with AI-powered features and scheduled pipeline execution.

## Current Project Structure

```
AURA/
├── frontend/                    # React 18 + TypeScript Frontend
│   ├── src/
│   │   ├── components/
│   │   │   ├── Pipelines/
│   │   │   │   ├── PipelinesPanel.tsx      # Main pipelines UI
│   │   │   │   └── PipelinesPanel.css      # Pipelines styling
│   │   │   ├── Layout/
│   │   │   │   └── ResizableLayout.tsx     # Resizable panel layout
│   │   │   ├── NavigationBar.tsx           # Top navigation
│   │   │   ├── NavigationBar.css
│   │   │   ├── ChatArea.tsx                # Chat interface
│   │   │   ├── ResultsArea.tsx             # Results display
│   │   │   ├── LeftSidebar.tsx             # Data sources sidebar
│   │   │   ├── LeftSidebar.css
│   │   │   ├── DatabaseConnector.tsx       # Database connection UI
│   │   │   ├── DatabaseConnector.css
│   │   │   ├── GlassBox.tsx                # Glass panel component
│   │   │   ├── GlassBox.css
│   │   │   ├── TrendAnalysis.tsx           # Trend analysis component
│   │   │   ├── TrendAnalysis.css
│   │   │   ├── DataTable.tsx               # Data table display
│   │   │   ├── DataVisualization.tsx       # Data visualization
│   │   │   ├── DataVisualization.css
│   │   │   ├── SqlDisplay.tsx              # SQL query display
│   │   │   ├── FileUpload.tsx              # File upload component
│   │   │   ├── FileUpload.css
│   │   │   ├── BackgroundParticles.tsx     # Animated background
│   │   │   ├── BackgroundParticles.css
│   │   │   ├── MessageList.tsx             # Message list
│   │   │   ├── ChatInput.tsx               # Chat input
│   │   │   ├── ErrorBoundary.tsx           # Error boundary
│   │   │   ├── ThemeToggle.tsx             # Theme toggle
│   │   │   ├── ThemeToggle.css
│   │   │   ├── ConnectionsPanel.tsx        # Connections panel
│   │   │   └── ConnectionsPanel.css
│   │   ├── contexts/
│   │   │   └── ThemeContext.tsx            # Theme context
│   │   ├── assets/                         # Images, fonts, etc
│   │   ├── plugins/                        # Plugin utilities
│   │   ├── App.tsx                         # Main app component
│   │   ├── App.css
│   │   ├── index.css
│   │   ├── main.tsx                        # React entry point
│   │   └── types.ts                        # TypeScript types
│   ├── package.json                        # Dependencies
│   ├── tsconfig.json                       # TypeScript config
│   ├── vite.config.ts                      # Vite config
│   └── index.html
│
├── aurabackend/                 # Python FastAPI Backend (8 Microservices)
│   ├── api_gateway/                        # API Gateway (port 8000)
│   ├── database/                           # Database Service (port 8002)
│   ├── code_generation_service/            # Code Generation (port 8003)
│   ├── scheduler_service/                  # Scheduler Service (port 8004)
│   │   ├── main.py                         # FastAPI app
│   │   ├── models.py                       # Data models
│   │   ├── coordinator.py                  # Job coordinator
│   │   ├── executor.py                     # Job executor
│   │   ├── worker.py                       # Background worker
│   │   └── repository.py                   # Database repository
│   ├── execution_sandbox/                  # Execution Sandbox (port 8007)
│   ├── knowledge_base/                     # Knowledge Base (port 8005)
│   ├── metadata_store/                     # Metadata Store (port 8006)
│   ├── orchestration_service/              # Orchestration (port 8001)
│   ├── shared/                             # Shared utilities
│   ├── mcp_core/                           # MCP protocol
│   ├── contracts/                          # Protocol buffers
│   ├── data/                               # Data storage
│   └── requirements.txt                    # Python dependencies
│
├── data/                        # Test data
│   └── test_files/
│       ├── products.csv
│       └── products.json
│
└── Scripts/                     # Helper scripts
    ├── start-all.ps1          # Start all services
    ├── start-scheduler.ps1    # Start scheduler service
    ├── start-database.ps1     # Start database service
    ├── start-docker.ps1       # Docker compose
    └── status.ps1             # Check service status

```

## Key Features

### Frontend (React 18 + TypeScript)
- **Chat Interface**: AI-powered conversation for data analysis
- **Database Connector**: Connect to databases
- **Data Visualization**: Charts and graphs
- **Pipeline Manager**: Schedule and execute data pipelines
- **Resizable Layout**: Flexible panel management
- **Theme Support**: Light/Dark mode toggle

### Backend (Python FastAPI)
- **8 Microservices**: Modular service architecture
- **Scheduler Service**: Job scheduling and execution (port 8004)
- **Database Service**: SQL query execution
- **Code Generation**: AI-powered SQL generation
- **Metadata Store**: Job and execution tracking
- **Background Worker**: Automated job execution

## Running the Application

### Start Frontend
```bash
cd frontend
npm install
npm run dev
# Opens on http://localhost:5173
```

### Start Backend Services
```bash
# Start all services
.\start-all.ps1

# OR start individual services
.\start-scheduler.ps1      # Port 8004
.\start-database.ps1       # Port 8002
```

### Access Application
- **Frontend**: http://localhost:5173
- **Scheduler API**: http://localhost:8004
- **Database API**: http://localhost:8002

## Recent Cleanup

### Removed Unused Files
- `SqlDisplay-fixed.tsx` - Duplicate SQL display file
- `ChartDisplay.tsx` - Unused component
- `VerticalSelector.tsx` - Unused component
- `VisualizationPanel.tsx` - Unused component
- `StrategicDemo.tsx` - Unused demo component
- `Header.tsx` - Unused header
- `DataDisplay.tsx` - Unused data display
- `JobCreator.tsx`, `JobDetails.tsx`, `JobList.tsx` - Moved to simplified PipelinesPanel
- `useScheduler.ts` - Unused hooks (not needed in simplified version)
- `schedulerApi.ts` - Unused API service

### Simplified Components
- **PipelinesPanel**: Reduced from complex multi-component setup to single minimal component
  - Removed JobCreator, JobDetails, JobList sub-components
  - Removed complex hooks and API calls
  - Now displays simple pipeline list with selection capability

## Component Dependencies

### Active Components Used in App.tsx
```
App.tsx
├── ThemeProvider (Context)
├── NavigationBar
├── ChatArea
├── ResultsArea
├── TrendAnalysis
├── ResizableLayout
├── LeftSidebar
├── GlassBox
├── BackgroundParticles
├── DatabaseConnector
├── PipelinesPanel (Pipelines/PipelinesPanel.tsx)
└── ErrorBoundary
```

## Technology Stack

### Frontend
- React 18
- TypeScript
- Vite 7.1.12
- React Hooks
- CSS3

### Backend
- Python 3.8+
- FastAPI
- SQLite / PostgreSQL
- Uvicorn
- APScheduler (Job scheduling)

## Next Steps for Development
1. Implement full pipeline CRUD operations with database
2. Integrate scheduler API with frontend
3. Add real-time job execution tracking
4. Implement authentication
5. Add comprehensive error handling
6. Create unit tests

---
**Last Updated**: January 22, 2026
**Status**: Cleaned & Optimized ✓
