# Undeploying and Cleaning Up

To stop incurring charges, you must undeploy the model from the endpoint. This
is a multi-step process if you don't already have the exact endpoint and
deployed model IDs.

## Example: Finding and Undeploying a Model

Here is a bash script demonstrating how to find the IDs and undeploy the model.

```bash
#!/bin/bash
# Example script to undeploy a model

PROJECT_ID=$(gcloud config get-value project)
: "${PROJECT_ID:?no gcloud project is set; ask the user for the Project ID}"
LOCATION_ID="us-central1"
# The model ID used during deployment.
# It is usually easiest to find via `gcloud ai models list`.

# 1. Find the Endpoint ID
echo "Listing endpoints in ${LOCATION_ID}:"
gcloud ai endpoints list --project=${PROJECT_ID} --region=${LOCATION_ID}

# (Assuming you extracted ENDPOINT_ID from the above output)
# ENDPOINT_ID="your_endpoint_id"

# 2. Find the Deployed Model ID
echo "Listing models in ${LOCATION_ID} to find model description:"
gcloud ai models list --project=${PROJECT_ID} --region=${LOCATION_ID}

# (Assuming you found the specific MODEL_ID)
# MODEL_ID="your_model_id"
# gcloud ai models describe ${MODEL_ID} \
#     --project=${PROJECT_ID} --region=${LOCATION_ID}
# (Extract the deployedModelId from the output)
# DEPLOYED_MODEL_ID="your_deployed_model_id"

# 3. Undeploy
echo "Undeploying model ${DEPLOYED_MODEL_ID} from endpoint ${ENDPOINT_ID}..."
gcloud ai endpoints undeploy-model ${ENDPOINT_ID} \
    --project=${PROJECT_ID} \
    --region=${LOCATION_ID} \
    --deployed-model-id=${DEPLOYED_MODEL_ID}

echo "Model undeployed."

# 4. Delete Endpoint
echo "Deleting endpoint ${ENDPOINT_ID}..."
gcloud ai endpoints delete ${ENDPOINT_ID} \
    --project=${PROJECT_ID} \
    --region=${LOCATION_ID} \
    --quiet
echo "Endpoint deleted."

# 5. Delete Model
echo "Deleting model ${MODEL_ID}..."
gcloud ai models delete ${MODEL_ID} \
    --project=${PROJECT_ID} \
    --region=${LOCATION_ID} \
    --quiet
echo "Model deleted."
```

> [!WARNING] Failing to undeploy a model will result in continuous charges for
> the allocated compute resources, even if you are not sending prediction
> requests. Always clean up after testing.
