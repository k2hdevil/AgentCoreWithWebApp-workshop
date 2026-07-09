"""
AgentCore Runtime Entrypoint — Memory + Gateway 통합

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AgentCore 서비스 매핑:
  - AgentCore Runtime  : BedrockAgentCoreApp으로 HTTP 서버 자동 구성
  - AgentCore Memory   : MemoryClient로 대화 이력 저장/조회 (STM)
  - AgentCore Gateway  : MCP 프로토콜로 외부 도구(Lambda) 호출
  - AgentCore Identity : Cognito OAuth M2M으로 Gateway 인증
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

호출 흐름:
  Client → invoke_agent_runtime API → Runtime(이 파일) → Agent 실행 → 응답 반환

  invoke(payload)
    ├── [Memory] 이전 대화 조회 (list_events)
    ├── [Identity] Cognito에서 OAuth 토큰 취득
    ├── [Gateway] MCP 연결 → 외부 도구 목록 획득
    ├── [Agent] AWSCostEstimatorAgent 실행
    │     ├── Code Interpreter (보안 샌드박스 계산)
    │     ├── AWS Pricing MCP (실시간 가격 데이터)
    │     └── Gateway MCP (markdown_to_email 등)
    └── [Memory] 현재 대화 저장 (create_event)

  ※ MEMORY_ID는 .bedrock_agentcore.yaml에서 동적으로 로드됩니다.
    배포 시 yaml 파일이 패키지에 포함되므로 하드코딩 불필요.
"""

import sys
import os
import logging

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from cost_estimator_agent.cost_estimator_agent import AWSCostEstimatorAgent
from bedrock_agentcore.runtime import BedrockAgentCoreApp

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [AgentCore Runtime] BedrockAgentCoreApp 인스턴스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
app = BedrockAgentCoreApp()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 환경 변수 기반 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REGION = os.environ.get('AWS_DEFAULT_REGION') or os.environ.get('AWS_REGION', 'us-west-2')

# Memory 설정 — .bedrock_agentcore.yaml에서 로드 또는 환경변수
MEMORY_ID = os.environ.get("MEMORY_ID")
if not MEMORY_ID:
    _yaml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bedrock_agentcore.yaml")
    if os.path.exists(_yaml_path):
        try:
            import yaml as _yaml
            with open(_yaml_path, "r") as _f:
                _cfg = _yaml.safe_load(_f)
            _agent_name = _cfg.get("default_agent", "")
            MEMORY_ID = _cfg.get("agents", {}).get(_agent_name, {}).get("memory", {}).get("memory_id")
        except Exception as _e:
            logger.warning(f"Failed to load MEMORY_ID from yaml: {_e}")
    else:
        logger.warning(f".bedrock_agentcore.yaml not found at {_yaml_path}")

if MEMORY_ID:
    logger.info(f"MEMORY_ID configured: {MEMORY_ID}")
else:
    logger.warning("MEMORY_ID not configured - memory features will be disabled")

ACTOR_ID = "webui_user"

# Gateway 설정 (AgentCore Gateway + Identity)
GATEWAY_URL = os.environ.get(
    "GATEWAY_URL",
    "https://awscostestimatorgateway-vpcqxftagh.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp"
)
OAUTH_SCOPE = os.environ.get("OAUTH_SCOPE", "InboundAuthorizerForCostEstimatorAgent/invoke")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [AgentCore Memory] 수동 대화 이력 관리
# - Runtime harness는 저장만 자동 처리, 조회/주입은 수동 필요
# - KEY POINT: MemoryClient는 lazy 초기화 (cold start 대응)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_memory_client = None


def _get_memory_client():
    """MemoryClient 싱글턴"""
    global _memory_client
    if _memory_client is None:
        try:
            from bedrock_agentcore.memory.client import MemoryClient
            _memory_client = MemoryClient(region_name=REGION)
            logger.info("MemoryClient initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize MemoryClient: {e}")
    return _memory_client


def _get_session_id():
    """Runtime 컨텍스트에서 세션 ID 추출"""
    try:
        from bedrock_agentcore.runtime.context import BedrockAgentCoreContext
        sid = BedrockAgentCoreContext.get_session_id()
        if sid:
            return sid
    except Exception:
        pass
    return "default_session"


def _retrieve_history(session_id):
    """이전 대화를 Memory에서 조회 (best-effort)"""
    if not MEMORY_ID:
        return ""
    client = _get_memory_client()
    if not client:
        return ""

    try:
        # TODO 2: Memory에서 이전 대화 이벤트를 조회하는 API 호출
        # HINT: AgentCore Memory의 이벤트 목록 조회 메서드입니다.
        # 참고: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/short-term-memory-operations.html
        events = client."_____"(
            memory_id=MEMORY_ID,
            actor_id=ACTOR_ID,
            session_id=session_id,
            max_results=____
        )
        if not events:
            logger.info("No previous conversation found in memory")
            return ""

        logger.info(f"Retrieved {len(events)} events from memory")
        lines = []
        for event in events:
            for item in event.get('payload', []):
                if 'conversational' in item:
                    msg = item['conversational']
                    role = msg.get('role', 'unknown')
                    text = msg.get('content', {}).get('text', '')
                    if text:
                        if len(text) > 500:
                            text = text[:500] + "..."
                        lines.append(f"[{role}]: {text}")

        if not lines:
            return ""
        return "Previous conversation in this session:\n" + "\n".join(lines)

    except Exception as e:
        logger.warning(f"Memory retrieval failed: {e}")
        return ""


def _store_conversation(session_id, user_input, result):
    """현재 대화를 Memory에 저장 (best-effort)"""
    if not MEMORY_ID:
        return
    client = _get_memory_client()
    if not client:
        return

    try:
        # TODO 3: Memory에 현재 대화를 저장하는 API 호출
        # HINT: AgentCore Memory에 새 이벤트를 생성하여 대화를 저장합니다.
        # 참고: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/short-term-memory-operations.html
        client."_____"(
            memory_id=MEMORY_ID,
            actor_id=ACTOR_ID,
            session_id=session_id,
            messages=[
                (user_input, "_____"),
                (result, "_____")
            ]
        )
        logger.info("Conversation stored in memory")
    except Exception as e:
        logger.warning(f"Memory storage failed: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [AgentCore Identity] Cognito OAuth M2M 토큰 취득
# - inbound_authorizer.json에서 Cognito 설정을 로드
# - 환경변수가 있으면 우선 사용
# - KEY POINT: Gateway 호출에 필요한 Bearer 토큰을 발급
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _get_gateway_access_token():
    """Cognito M2M client_credentials 플로우로 OAuth 토큰 취득"""
    try:
        import requests as http_requests

        # 환경변수 우선 확인
        token_endpoint = os.environ.get("TOKEN_ENDPOINT")
        client_id = os.environ.get("COGNITO_CLIENT_ID")
        client_secret = os.environ.get("COGNITO_CLIENT_SECRET")

        # 환경변수가 없으면 inbound_authorizer.json에서 로드
        if not all([token_endpoint, client_id, client_secret]):
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inbound_authorizer.json")
            if os.path.exists(config_path):
                import json as _json
                with open(config_path, "r") as f:
                    identity_config = _json.load(f)
                cognito = identity_config.get("cognito", {})
                token_endpoint = token_endpoint or cognito.get("token_endpoint")
                client_id = client_id or cognito.get("client_id")
                client_secret = client_secret or cognito.get("client_secret")
                logger.info("Loaded Cognito credentials from inbound_authorizer.json")
            else:
                logger.warning(f"inbound_authorizer.json not found at {config_path}")
                return None

        if not all([token_endpoint, client_id, client_secret]):
            logger.warning("Cognito credentials incomplete")
            return None

        response = http_requests.post(
            token_endpoint,
            data={
                # TODO 4: OAuth2 client_credentials 플로우의 grant_type 값
                # HINT: M2M(Machine-to-Machine) 인증에 사용되는 OAuth2 grant type입니다.
                # 참고: https://docs.aws.amazon.com/cognito/latest/developerguide/token-endpoint.html
                "grant_type": "_____",
                "scope": OAUTH_SCOPE,
            },
            auth=(client_id, client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )

        if response.status_code == 200:
            token = response.json().get("access_token")
            logger.info("Gateway OAuth token obtained via Cognito")
            return token
        else:
            logger.warning(f"Cognito token request failed: {response.status_code} {response.text[:200]}")
            return None
    except Exception as e:
        logger.warning(f"Failed to get Gateway token: {e}")
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [AgentCore Gateway] MCP 클라이언트 생성
# - KEY POINT: Bearer 토큰을 Authorization 헤더에 포함해야 접근 가능
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _get_gateway_tools(access_token):
    """Gateway MCP에 연결하여 MCPClient 반환"""
    try:
        from strands.tools.mcp import MCPClient
        from mcp.client.streamable_http import streamablehttp_client

        def create_transport():
            # TODO 5: Streamable HTTP MCP 전송 계층 생성
            # HINT: Gateway URL과 Bearer 토큰을 Authorization 헤더로 전달합니다.
            # 참고: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-connect-mcp.html
            return streamablehttp_client(
                "_____",
                headers={"_____": f"Bearer {access_token}"}
            )

        mcp_client = MCPClient(create_transport)
        return mcp_client
    except Exception as e:
        logger.warning(f"Failed to connect to Gateway MCP: {e}")
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [AgentCore Runtime] 메인 엔트리포인트
# - @app.entrypoint: Runtime이 HTTP 요청을 받으면 이 함수를 호출
# - 세션 ID는 BedrockAgentCoreContext에서 추출 (클라이언트의 runtimeSessionId)
# - KEY POINT: 모든 외부 연동은 best-effort — 실패해도 핵심 기능은 동작
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TODO 1: Runtime 엔트리포인트 데코레이터
# HINT: BedrockAgentCoreApp의 엔트리포인트를 등록하는 데코레이터입니다.
# 참고: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-getting-started-toolkit.html
@app."_____"
def invoke(payload):
    user_input = payload.get("prompt")
    session_id = _get_session_id()

    logger.info(f"Session: {session_id} | Input: {user_input[:80]}...")

    # ── [Memory] 이전 대화 조회 ──
    history = _retrieve_history(session_id)
    if history:
        logger.info("Injecting conversation history")
        prompt = f"{history}\n\nNew request from user:\n{user_input}"
    else:
        prompt = user_input

    # ── [Gateway] 외부 도구 연결 (best-effort) ──
    gateway_mcp_client = None
    try:
        logger.info(f"Attempting Gateway connection: {GATEWAY_URL}")
        access_token = _get_gateway_access_token()
        if access_token:
            logger.info(f"Token obtained, length={len(access_token)}")
            gateway_mcp_client = _get_gateway_tools(access_token)
            if gateway_mcp_client:
                logger.info("Gateway MCP client created successfully")
            else:
                logger.warning("Gateway MCP client creation returned None")
        else:
            logger.warning("No access token obtained - Gateway will not be available")
    except Exception as e:
        logger.error(f"Gateway setup failed (continuing without): {e}", exc_info=True)

    # ── [Agent] 비용 견적 에이전트 실행 ──
    agent = AWSCostEstimatorAgent(region=REGION)

    if gateway_mcp_client:
        # Gateway 도구 포함 모드 (markdown_to_email 등 사용 가능)
        logger.info("Running agent WITH Gateway tools (markdown_to_email available)")
        result = agent.estimate_costs_with_gateway(prompt, gateway_mcp_client)
    else:
        # 기본 모드 (Code Interpreter + Pricing MCP만)
        logger.info("Running agent without Gateway tools")
        result = agent.estimate_costs(prompt)

    # ── [Memory] 현재 대화 저장 ──
    _store_conversation(session_id, user_input, result)

    return result


if __name__ == "__main__":
    app.run()
