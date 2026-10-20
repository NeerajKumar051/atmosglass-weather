# AtmosGlass Weather

A desktop weather application built with Python, React, TypeScript,
and pywebview, using the Open-Meteo weather APIs.

> Status: Active development

## Features

- City search using weather geocoding
- Current weather conditions
- Hourly and 7-day forecast data
- Python backend with a JavaScript bridge
- Glassmorphism-inspired desktop interface
- Automated Python tests

## Tech Stack

**Backend:** Python, pywebview, Requests  
**Frontend:** React, TypeScript, Vite, CSS  
**Weather Data:** Open-Meteo  
**Testing:** pytest

## Prerequisites

- Python 3.12+
- Node.js LTS and npm
- Git

## Setup on Windows

Clone the repository:

```powershell
git clone https://github.com/NeerajKumar051/atmosglass-weather.git
cd atmosglass-weather


Create a Python environment and install dependencies:
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install pywebview requests


Build the frontend:
cd frontend
npm.cmd install
npm.cmd run build
cd ..


Launch the desktop application:
.\.venv\Scripts\python.exe main.py


Run Tests
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest -q


Weather Data
Weather data is provided by Open-Meteo.

https://open-meteo.com/