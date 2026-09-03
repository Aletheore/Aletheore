def build_request_parameters(token_request):
    return {
        "grant_type": "client_credentials",
        "client_id": token_request.client_id,
        "client_secret": token_request.ClientSecret,
    }
