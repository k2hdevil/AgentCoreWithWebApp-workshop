# AWS Cost Estimator — Amazon Bedrock AgentCore Demo

실시간 AWS 가격 데이터를 기반으로 아키텍처 비용을 견적하는 AI 에이전트입니다.
Amazon Bedrock AgentCore의 주요 기능(Runtime, Memory, Gateway, Identity, Code Interpreter)을 하나의 웹 애플리케이션으로 통합한 데모입니다.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Browser (web/static/index.html)                                    │
│    - 채팅 UI, 마크다운 렌더링, 세션 관리                              │
└──────────────────────┬──────────────────────────────────────────────┘
                       │ POST /api/chat
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Web Backend (web/app.py)                                           │
│    - FastAPI 서버                                                    │
│    - boto3 invoke_agent_runtime() 호출                               │
│    - SSE 스트리밍 응답 처리                                           │
└──────────────────────┬──────────────────────────────────────────────┘
                       │ invoke_agent_runtime API
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│  AgentCore Runtime (agent/invoke.py)                                │
│                                                                     │
│  ┌─── Memory ────────────────────────────────────────────────────┐  │
│  │  list_events() → 이전 대화 조회                                │  │
│  │  create_event() → 현재 대화 저장                               │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌─── Agent (Strands + Claude Sonnet) ───────────────────────────┐  │
│  │                                                               │  │
│  │  ┌─────────────────┐  ┌──────────────────┐  ┌─────────────┐  │  │
│  │  │ Code Interpreter│  │ AWS Pricing MCP  │  │ Gateway MCP │  │  │
│  │  │ (보안 계산)      │  │ (가격 데이터)     │  │ (이메일 등) │  │  │
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
│  │  Cognito OAuth M2M → Bearer Token → Gateway 인증              │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

## AgentCore 서비스 통합

| 서비스 | 역할 | 파일 |
|--------|------|------|
| **Runtime** | 에이전트를 클라우드에 배포/실행 | `agent/invoke.py` |
| **Code Interpreter** | 보안 샌드박스에서 비용 계산 코드 실행 | `agent/cost_estimator_agent/cost_estimator_agent.py` |
| **Memory (STM)** | 세션 내 대화 기록 저장/조회 | `agent/invoke.py` |
| **Identity** | Cognito OAuth M2M으로 Gateway 인증 | `agent/invoke.py` |
| **Gateway** | Lambda(markdown_to_email) 등 외부 도구 호출 | `agent/cost_estimator_agent/cost_estimator_agent.py` |
| **Observability** | CloudWatch 로그 + X-Ray 트레이스 | Runtime 자동 구성 |

## Project Structure

```
.
├── agent/                              # AgentCore Runtime에 배포되는 코드
│   ├── invoke.py                       # 엔트리포인트 (Memory + Gateway 통합)
│   ├── requirements.txt                # 런타임 의존성
│   ├── inbound_authorizer.json         # Cognito/Identity 설정 (Gateway 인증용)
│   ├── .bedrock_agentcore.yaml         # 에이전트 ARN, 메모리 ID (agentcore deploy가 생성)
│   └── cost_estimator_agent/
│       ├── __init__.py
│       ├── config.py                   # 시스템 프롬프트, 모델 설정
│       └── cost_estimator_agent.py     # 에이전트 핵심 로직
├── web/                                # 웹 UI (프론트엔드 + 백엔드)
│   ├── app.py                          # FastAPI 백엔드
│   └── static/
│       └── index.html                  # 채팅 프론트엔드
├── pyproject.toml                      # Python 프로젝트 설정
└── README.md                           # 이 파일
```

## Prerequisites

- Python 3.12+
- AWS 계정 (Bedrock AgentCore 접근 권한)
- AWS CLI 또는 환경변수로 자격증명 설정
- `uv` 패키지 매니저

## Quick Start

### 1. 에이전트 배포

```bash
cd agent
uv run agentcore configure \
  --entrypoint ./invoke.py \
  --name cost_estimator_agent \
  --requirements-file ./requirements.txt \
  --deployment-type direct_code_deploy \
  --region us-west-2

uv run agentcore deploy --env AWS_REGION=us-west-2
```

### 2. 웹 서버 실행

```bash
cd web
uv run uvicorn app:app --reload --port 8080
```

### 3. 브라우저에서 접속

http://127.0.0.1:8080

## How It Works

1. 사용자가 AWS 아키텍처를 설명 (예: "EC2 t3.micro 24/7 비용")
2. 웹 백엔드가 AgentCore Runtime의 `invoke_agent_runtime` API 호출
3. Runtime 내부에서:
   - Memory에서 이전 대화 컨텍스트 조회
   - Claude Sonnet이 아키텍처 분석
   - AWS Pricing MCP로 실시간 가격 데이터 조회
   - Code Interpreter로 비용 계산
   - (선택) Gateway를 통해 이메일 발송
   - Memory에 현재 대화 저장
4. 응답이 SSE 스트리밍으로 브라우저에 실시간 표시

## Key Design Decisions

- **Best-effort 패턴**: Memory/Gateway 실패 시에도 핵심 기능(비용 견적)은 정상 동작
- **Lazy initialization**: Cold start 30초 제한 내 로드를 위해 MemoryClient를 지연 초기화
- **세션 연속성**: 같은 `runtimeSessionId`를 재사용하면 Memory가 대화 맥락 연결
- **리소스 관리**: Context manager로 Code Interpreter 세션 라이프사이클 관리 (누수 방지)
- **멀티바이트 안전**: UTF-8 incremental decoder로 한글 응답의 청크 경계 문제 해결

## Environment Variables

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `AGENT_ARN` | AgentCore Runtime ARN | config yaml에서 로드 |
| `AWS_REGION` | AWS 리전 | us-west-2 |
| `MEMORY_ID` | AgentCore Memory ID | config에서 자동 |
| `GATEWAY_URL` | Gateway MCP 엔드포인트 | 하드코딩 기본값 |

## Deploying to a Different AWS Account

다른 AWS 계정에 이 앱을 배포할 때 변경해야 하는 항목입니다.

### 1. 필수 변경 사항

| 파일 | 변경 항목 | 설명 |
|------|----------|------|
| `agent/.bedrock_agentcore.yaml` | `aws.account` | 새 계정 ID (12자리) |
| `agent/.bedrock_agentcore.yaml` | `aws.execution_role` | 새 계정의 IAM 역할 ARN |
| `agent/.bedrock_agentcore.yaml` | `aws.s3_path` | 새 계정의 S3 버킷 (자동 생성 가능) |
| `agent/.bedrock_agentcore.yaml` | `bedrock_agentcore.agent_id` | 배포 후 자동 할당됨 (삭제하고 재배포) |
| `agent/.bedrock_agentcore.yaml` | `bedrock_agentcore.agent_arn` | 배포 후 자동 할당됨 (삭제하고 재배포) |
| `agent/.bedrock_agentcore.yaml` | `memory.memory_id` | 새 메모리 생성 후 ID 교체 |
| `agent/.bedrock_agentcore.yaml` | `memory.memory_arn` | 새 메모리 생성 후 ARN 교체 |

### 2. Identity/Gateway 사용 시 추가 변경

| 파일 | 변경 항목 | 설명 |
|------|----------|------|
| `agent/inbound_authorizer.json` | 전체 재생성 | Identity 설정 후 새 파일로 교체 |
| `agent/invoke.py` | `MEMORY_ID` 기본값 | 새 메모리 ID로 변경 |
| `agent/invoke.py` | `GATEWAY_URL` 기본값 | 새 Gateway 엔드포인트로 변경 |

### 3. 권장 배포 절차 (새 계정)

```bash
# 1. agent/.bedrock_agentcore.yaml 삭제 (새로 생성됨)
rm agent/.bedrock_agentcore.yaml

# 2. 에이전트 configure (새 계정에 맞게 자동 설정)
cd agent
uv run agentcore configure \
  --entrypoint ./invoke.py \
  --name cost_estimator_agent \
  --requirements-file ./requirements.txt \
  --deployment-type direct_code_deploy \
  --region us-west-2

# 3. 배포
uv run agentcore deploy --env AWS_REGION=us-west-2

# 4. (선택) Identity 설정 후 inbound_authorizer.json 교체
# Gateway 사용 시, 새 Cognito 설정 파일을 agent/ 폴더에 배치 후 재배포
uv run agentcore deploy --env AWS_REGION=us-west-2 --env GATEWAY_URL=<새 Gateway URL>

# 5. 웹 서버 실행
cd ../web
uv run uvicorn app:app --reload --port 8080
```

### 4. IAM 권한 요구사항

새 계정의 에이전트 실행 역할에 필요한 권한:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock-agentcore:*"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:log-group:/aws/bedrock-agentcore/*"
    }
  ]
}
```

> **Note**: 프로덕션 환경에서는 `Resource: "*"` 대신 구체적인 ARN으로 범위를 좁혀야 합니다.

## License

This project is licensed under the MIT-0 License.
