#!/bin/bash
PROJECT_ID=$(gcloud config get-value project)
SERVICE_NAME="seattle-retail-broker"

gcloud run deploy $SERVICE_NAME \
  --source . \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --min-instances 1 \
  --memory 2Gi \
  --cpu 2 \
  --set-env-vars PROJECT_ID=$PROJECT_ID
