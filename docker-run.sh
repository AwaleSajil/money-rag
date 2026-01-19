#!/bin/bash

# MoneyRAG Docker Run Script
# This script helps you run the application easily

set -e

echo "🐳 MoneyRAG Docker Setup"
echo "========================"
echo "ℹ️  Note: API keys are entered through the web UI"
echo ""

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker Desktop."
    exit 1
fi

# Create data and logs directories
mkdir -p data logs

echo ""
echo "Choose an option:"
echo "1) Build and run (first time or after code changes)"
echo "2) Run existing container"
echo "3) Stop container"
echo "4) View logs"
echo "5) Clean up (remove containers and images)"
echo ""
read -p "Enter choice [1-5]: " choice

case $choice in
    1)
        echo "🔨 Building Docker image..."
        docker-compose build
        echo "🚀 Starting container..."
        docker-compose up -d
        echo "✅ Application is running at http://localhost:8501"
        echo "📋 View logs with: docker-compose logs -f"
        ;;
    2)
        echo "🚀 Starting container..."
        docker-compose up -d
        echo "✅ Application is running at http://localhost:8501"
        ;;
    3)
        echo "🛑 Stopping container..."
        docker-compose down
        echo "✅ Container stopped"
        ;;
    4)
        echo "📋 Showing logs (Ctrl+C to exit)..."
        docker-compose logs -f
        ;;
    5)
        echo "🧹 Cleaning up..."
        docker-compose down -v
        docker rmi money_rag-money-rag 2>/dev/null || true
        echo "✅ Cleanup complete"
        ;;
    *)
        echo "❌ Invalid choice"
        exit 1
        ;;
esac
