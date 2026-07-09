"""
Setup Cognito + Identity Provider for the current AWS account.

This creates:
1. Cognito User Pool + App Client (M2M)
2. AgentCore OAuth2 Credential Provider
3. Updates inbound_authorizer.json with new values

Usage:
    cd agent
    uv run python setup_identity.py
"""

import json
import time
import logging
from pathlib import Path

import boto3
import requests
from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

CONFIG_FILE = Path(__file__).parent.parent / "agent" / "inbound_authorizer.json"
PROVIDER_NAME = "inbound-identity-for-cost-estimator-agent"


def main():
    region = boto3.Session().region_name or "us-west-2"
    account_id = boto3.client("sts").get_caller_identity()["Account"]

    logger.info(f"Account: {account_id}, Region: {region}")

    # Step 1: Create Cognito User Pool + App Client
    logger.info("Creating Cognito OAuth authorizer...")
    gateway_client = GatewayClient(region_name=region)
    cognito_result = gateway_client.create_oauth_authorizer_with_cognito(
        "InboundAuthorizerForCostEstimatorAgent"
    )

    user_pool_id = cognito_result["client_info"]["user_pool_id"]
    discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration"

    cognito_config = {
        "client_id": cognito_result["client_info"]["client_id"],
        "client_secret": cognito_result["client_info"]["client_secret"],
        "token_endpoint": cognito_result["client_info"]["token_endpoint"],
        "discovery_url": discovery_url,
        "scope": cognito_result["client_info"]["scope"],
        "user_pool_id": user_pool_id,
        "region": region,
    }
    logger.info(f"Cognito created: {user_pool_id}")

    # Step 2: Wait for OIDC endpoint
    logger.info("Waiting for OIDC endpoint...")
    for i in range(12):
        try:
            resp = requests.get(discovery_url, timeout=10)
            if resp.status_code == 200 and "issuer" in resp.json():
                logger.info("OIDC endpoint ready")
                break
        except Exception:
            pass
        time.sleep(5)

    # Step 3: Create OAuth2 Credential Provider
    logger.info("Creating Identity Provider...")
    # TODO 13: AgentCore Identity 제어 플레인 boto3 클라이언트 서비스명
    # HINT: AgentCore의 제어 플레인(Control Plane) 서비스 이름입니다. 리소스 생성/삭제에 사용합니다.
    # 참고: https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore-control.html
    identity_client = boto3.client("_____", region_name=region)

    # Delete existing provider if any
    try:
        identity_client.delete_oauth2_credential_provider(name=PROVIDER_NAME)
        logger.info("Deleted existing provider")
        time.sleep(5)
    except Exception:
        pass

    # TODO 12: AgentCore Identity에 OAuth2 자격증명 공급자를 생성하는 API 호출
    # HINT: bedrock-agentcore-control 클라이언트의 OAuth2 credential provider 생성 메서드입니다.
    # 참고: https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore-control/client/create_oauth2_credential_provider.html
    response = identity_client."_____"(
        name=PROVIDER_NAME,
        credentialProviderVendor="_____",
        oauth2ProviderConfigInput={
            "customOauth2ProviderConfig": {
                "clientId": cognito_config["client_id"],
                "clientSecret": cognito_config["client_secret"],
                "oauthDiscovery": {"discoveryUrl": cognito_config["discovery_url"]},
            }
        },
    )

    provider_config = {
        "name": PROVIDER_NAME,
        "arn": response["credentialProviderArn"],
    }
    logger.info(f"Provider created: {provider_config['arn']}")

    # Step 4: Save config
    config = {
        "cognito": cognito_config,
        "provider": provider_config,
    }

    with CONFIG_FILE.open("w") as f:
        json.dump(config, f, indent=2)

    logger.info(f"Configuration saved to: {CONFIG_FILE}")
    print()
    print(json.dumps(config, indent=2))
    print()
    logger.info("Done! Redeploy agent to apply: uv run agentcore deploy --env AWS_REGION=us-west-2")


if __name__ == "__main__":
    main()
