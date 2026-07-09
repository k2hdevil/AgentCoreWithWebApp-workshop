# Amazon Bedrock AgentCore 워크샵 — 빈칸 채우기 실습

실시간 AWS 가격 데이터를 기반으로 아키텍처 비용을 견적하는 AI 에이전트를 구축하며,
Amazon Bedrock AgentCore의 핵심 기능(Runtime, Memory, Gateway, Identity, Code Interpreter)을 학습합니다.

## 실습 목표

코드의 빈칸(`"_____"` 또는 `____`)을 채워 AgentCore 에이전트를 완성하세요.
각 빈칸 위에 `# HINT:` 주석과 공식 문서 링크가 제공됩니다.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Browser (web/static/index.html)                                    │
│    - 채팅 UI, 마크다운 렌더링, 세션 관리                              │
└──────────────────────┬──────────────────────────────────────────────┘
                       │ POST /api/chat
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Web Backend (web/app.py)                        [TODO 9~11]        │
│    - FastAPI 서버                                                    │
│    - boto3 invoke_agent_runtime() 호출                               │
│    - SSE 스트리밍 응답 처리                                           │
└──────────────────────┬──────────────────────────────────────────────┘
                       │ invoke_agent_runtime API
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│  AgentCore Runtime (agent/invoke.py)             [TODO 1~5]         │
│                                                                     │
│  ┌─── Memory ────────────────────────────────────────────────────┐  │
│  │  list_events() → 이전 대화 조회            [TODO 2]            │  │
│  │  create_event() → 현재 대화 저장           [TODO 3]            │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌─── Agent (Strands + Claude Sonnet) ───────────────────────────┐  │
│  │                                                               │  │
│  │  ┌─────────────────┐  ┌──────────────────┐  ┌─────────────┐  │  │
│  │  │ Code Interpreter│  │ AWS Pricing MCP  │  │ Gateway MCP │  │  │
│  │  │ [TODO 6]        │  │ [TODO 7]         │  │ [TODO 5]    │  │  │
│  │  └────────┬────────┘  └────────┬─────────┘  └──────┬──────┘  │  │
│  │           │                    │                    │         │  │
│  └───────────┼────────────────────┼────────────────────┼─────────┘  │
│              ▼                    ▼                    ▼             │
│  ┌────────────────┐  ┌─────────────────┐  ┌────────────────────┐   │
│  │ AgentCore      │  │ AWS Pricing API │  │ AgentCore Gateway  │   │
│  │ Code Interpreter│  │ (us-east-1)    │  │  → Lambda → SES   │   │
│  └────────────────┘  └─────────────────┘  └────────────────────┘   │
│                                                                     │
│  ┌─── Identity ──────────────────────────────────────────────────┐  │
│  │  Cognito OAuth M2M → Bearer Token → Gateway 인증 [TODO 4]    │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

## 빈칸 목록 (총 17개 TODO)

### 파일 1: `agent/invoke.py` — Runtime 엔트리포인트

| TODO | 난이도 | 설명 | 관련 서비스 |
|------|--------|------|-------------|
| **TODO 1** | ★★☆ | Runtime 엔트리포인트 데코레이터 | AgentCore Runtime |
| **TODO 2** | ★★★ | Memory에서 이전 대화 조회 API + max_results 값 | AgentCore Memory |
| **TODO 3** | ★★★ | Memory에 대화 저장 API + 메시지 역할(role) 지정 | AgentCore Memory |
| **TODO 4** | ★★☆ | OAuth2 client_credentials 플로우의 grant_type | AgentCore Identity |
| **TODO 5** | ★★★ | Gateway MCP 연결 시 URL과 인증 헤더 | AgentCore Gateway |

### 파일 2: `agent/cost_estimator_agent/cost_estimator_agent.py` — 에이전트 핵심 로직

| TODO | 난이도 | 설명 | 관련 서비스 |
|------|--------|------|-------------|
| **TODO 6** | ★★☆ | Code Interpreter invoke 액션명 + 언어 지정 | Code Interpreter |
| **TODO 7** | ★★☆ | AWS Pricing MCP Server 패키지명 | MCP (Pricing) |
| **TODO 8** | ★☆☆ | Strands Agent 생성 시 도구 목록 파라미터 | Strands Agents |
| **TODO 15** | ★☆☆ | Code Interpreter 세션 시작 메서드 | Code Interpreter |

### 파일 3: `web/app.py` — FastAPI 백엔드

| TODO | 난이도 | 설명 | 관련 서비스 |
|------|--------|------|-------------|
| **TODO 9** | ★★★ | 에이전트 런타임 호출 메서드명 | AgentCore Runtime |
| **TODO 10** | ★★☆ | 에이전트 ARN 파라미터명 | AgentCore Runtime |
| **TODO 11** | ★★★ | 세션 ID 파라미터명 (최소 33자 제약) | AgentCore Runtime |
| **TODO 14** | ★★☆ | AgentCore 데이터 플레인 boto3 서비스명 + read_timeout 값 | AgentCore Runtime |

### 파일 4: `identity/setup_identity.py` — Identity 설정

| TODO | 난이도 | 설명 | 관련 서비스 |
|------|--------|------|-------------|
| **TODO 12** | ★★★ | OAuth2 credential provider 생성 API + vendor 값 | AgentCore Identity |
| **TODO 13** | ★★☆ | AgentCore 제어 플레인 boto3 서비스명 | AgentCore Identity |

### 파일 5: `gateway/setup_outbound_gateway.py` — Gateway 설정

| TODO | 난이도 | 설명 | 관련 서비스 |
|------|--------|------|-------------|
| **TODO 16** | ★★★ | Gateway Target 설정의 Lambda 키 이름 | AgentCore Gateway |
| **TODO 17** | ★★☆ | credentialProviderType 값 (IAM 역할 방식) | AgentCore Gateway |

## 진행 순서

### Step 1: Identity 설정 (`identity/setup_identity.py`)

Cognito User Pool과 AgentCore Identity Provider를 생성합니다.

```bash
cd identity
uv run python setup_identity.py
```

**학습 포인트:**
- `bedrock-agentcore-control` 클라이언트로 Identity 리소스 관리
- OAuth2 credential provider 개념 이해

### Step 2: 에이전트 핵심 로직 (`agent/cost_estimator_agent/cost_estimator_agent.py`)

Code Interpreter와 MCP 도구를 연결하는 에이전트를 구현합니다.

**학습 포인트:**
- Code Interpreter 세션 시작/실행/정리 라이프사이클
- MCP(Model Context Protocol)로 외부 도구 연결
- Strands Agent에 도구 리스트 전달

### Step 3: Gateway 설정 (`gateway/setup_outbound_gateway.py`)

Lambda 함수를 AgentCore Gateway에 MCP Target으로 등록합니다.

```bash
cd gateway
./deploy.sh your-verified-email@example.com   # Lambda 배포
uv run python setup_outbound_gateway.py        # Gateway + Target 생성
```

**학습 포인트:**
- Gateway Target의 `targetConfiguration.mcp.lambda` 구조
- `credentialProviderType`으로 인증 방식 지정
- Lambda를 MCP 도구로 노출하는 패턴

### Step 4: Runtime 엔트리포인트 (`agent/invoke.py`)

Memory, Gateway, Identity를 통합한 Runtime 엔트리포인트를 완성합니다.

**학습 포인트:**
- `@app.entrypoint` 데코레이터로 Runtime 함수 등록
- Memory의 list_events/create_event로 대화 맥락 관리
- Cognito client_credentials 플로우로 Gateway 인증
- streamablehttp_client로 Gateway MCP 연결

```bash
cd agent
uv run agentcore deploy --env AWS_REGION=us-west-2
```

### Step 5: 웹 백엔드 (`web/app.py`)

배포된 에이전트를 호출하는 웹 인터페이스를 완성합니다.

**학습 포인트:**
- `invoke_agent_runtime` API의 파라미터 구조
- `runtimeSessionId`로 세션 연속성 확보
- 스트리밍 응답 처리

```bash
cd web
uv run uvicorn app:app --reload --port 8080
```

### Step 6: 동작 확인

http://127.0.0.1:8080 에 접속하여 순서대로 테스트합니다.

1. **Runtime 동작 확인** — "서울 리전의 EC2 t3.micro 24/7 비용 견적" 입력
2. **Memory 동작 확인** — "버지니아 리전은 어떤가요?" 입력 (이전 대화 맥락을 기억하는지 확인)
3. **Gateway 동작 확인** — "버지니아 리전과 서울 리전을 비교한 견적을 your-verified-email@example.com으로 보내주세요" 입력

## 빈칸 규칙

| 표기 | 의미 | 예시 |
|------|------|------|
| `"_____"` (따옴표 + 밑줄 5개) | 문자형 값을 채워야 함 | `"entrypoint"`, `"client_credentials"` |
| `____` (밑줄 4개) | 숫자형 값을 채워야 함 | `6`, `900` |

## 참고 문서

| 서비스 | 공식 문서 |
|--------|----------|
| AgentCore Runtime | https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-getting-started-toolkit.html |
| AgentCore Memory | https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/short-term-memory-operations.html |
| AgentCore Code Interpreter | https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-execute-code.html |
| Code Interpreter 세션 시작 | https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-start-session.html |
| AgentCore Identity | https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore-control/client/create_oauth2_credential_provider.html |
| AgentCore Control Plane (boto3) | https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore-control.html |
| AgentCore Data Plane (boto3) | https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore.html |
| AgentCore Gateway | https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-connect-mcp.html |
| Gateway Target 생성 | https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-create-target.html |
| invoke_agent_runtime API | https://docs.aws.amazon.com/botocore/latest/reference/services/bedrock-agentcore/client/invoke_agent_runtime.html |
| Cognito Token Endpoint | https://docs.aws.amazon.com/cognito/latest/developerguide/token-endpoint.html |
| Strands Agents MCP | https://strandsagents.com/latest/user-guide/concepts/tools/mcp/ |
| AWS Pricing MCP Server | https://awslabs.github.io/mcp/servers/aws-pricing-mcp-server/ |

## Prerequisites

- Python 3.12+
- AWS 계정 (Bedrock AgentCore 접근 권한)
- AWS CLI 또는 환경변수로 자격증명 설정
- `uv` 패키지 매니저

## Project Structure

```
.
├── agent/                              # AgentCore Runtime에 배포되는 코드
│   ├── invoke.py                       # [TODO 1~5] 엔트리포인트
│   ├── requirements.txt                # 런타임 의존성
│   ├── inbound_authorizer.json         # Cognito/Identity 설정
│   └── cost_estimator_agent/
│       ├── config.py                   # 시스템 프롬프트, 모델 설정
│       └── cost_estimator_agent.py     # [TODO 6~8, 15] 에이전트 핵심 로직
├── web/                                # 웹 UI
│   ├── app.py                          # [TODO 9~11, 14] FastAPI 백엔드
│   └── static/index.html              # 채팅 프론트엔드
├── identity/
│   └── setup_identity.py              # [TODO 12~13] Identity 설정
├── gateway/
│   ├── setup_outbound_gateway.py      # [TODO 16~17] Gateway 설정
│   ├── src/app.py                     # Lambda 함수 (markdown_to_email)
│   └── template.yaml                  # SAM 템플릿
├── pyproject.toml
└── README.md                           # 이 파일 (워크샵 가이드)
```

## License

This project is licensed under the MIT-0 License.

## 제작 정보

- **원본 프로젝트**: [sample-amazon-bedrock-agentcore-onboarding](https://github.com/Kota-Kudo/sample-amazon-bedrock-agentcore-onboarding) — Amazon Bedrock AgentCore 온보딩 샘플 코드
- **웹 애플리케이션 통합 및 워크샵 컨텐츠 생성**: [Kiro](https://kiro.dev) (AI-powered IDE)를 활용하여 개별 샘플 코드를 하나의 웹 애플리케이션으로 통합하고, 빈칸 채우기 워크샵 형식으로 변환
- **컨텐츠 검수**: Human-in-the-Loop (HITL) 방식으로 빈칸 난이도, 힌트 정확성, 진행 순서를 검증 및 조정
