"""
AWS Cost Estimation Agent — Code Interpreter + MCP Pricing + Gateway 통합

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AgentCore 서비스 매핑:
  - AgentCore Code Interpreter : 보안 샌드박스에서 Python 코드 실행
  - AWS Pricing MCP Server     : 실시간 AWS 가격 데이터 조회 (MCP 프로토콜)
  - AgentCore Gateway          : 외부 도구(markdown_to_email Lambda) 호출
  - Strands Agents             : LLM 기반 에이전트 프레임워크 (도구 오케스트레이션)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

에이전트 도구 구성:
  - execute_cost_calculation : Code Interpreter로 비용 계산 코드 실행
  - get_pricing (MCP)        : AWS Pricing API에서 가격 데이터 조회
  - markdown_to_email (MCP)  : Gateway를 통해 Lambda → SES로 이메일 발송
"""

import logging
import os
import shutil
import traceback
import boto3
from contextlib import contextmanager
from typing import Generator, AsyncGenerator
from strands import Agent, tool
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from strands.handlers.callback_handler import null_callback_handler
from botocore.config import Config
from mcp import stdio_client, StdioServerParameters
from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter
from cost_estimator_agent.config import (
    SYSTEM_PROMPT,
    COST_ESTIMATION_PROMPT,
    DEFAULT_MODEL,
    LOG_FORMAT
)

# Configure comprehensive logging for debugging and monitoring
logging.basicConfig(
    level=logging.ERROR,  # Set to ERROR by default, can be changed to DEBUG for more details
    format=LOG_FORMAT,
    handlers=[logging.StreamHandler()]
)

# Enable Strands debug logging for detailed agent behavior
logging.getLogger("strands").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class AWSCostEstimatorAgent:
    """
    AWS 비용 견적 에이전트
    
    ┌─────────────────────────────────────────────────────────┐
    │  Strands Agent (LLM: Claude Sonnet)                     │
    │    ├── @tool execute_cost_calculation                    │
    │    │     └── [Code Interpreter] 보안 샌드박스 실행       │
    │    ├── MCP: get_pricing, get_pricing_service_codes, ... │
    │    │     └── [AWS Pricing MCP Server] uvx로 실행        │
    │    └── MCP: markdown_to_email (Gateway 모드 시)         │
    │          └── [AgentCore Gateway] → Lambda → SES         │
    └─────────────────────────────────────────────────────────┘
    
    주요 메서드:
      estimate_costs()              — 기본 견적 (Code Interpreter + Pricing)
      estimate_costs_with_gateway() — Gateway 도구 포함 견적 (+ 이메일 발송)
      estimate_costs_stream()       — 스트리밍 응답 (SSE)
    """
    
    def __init__(self, region: str = ""):
        """
        Initialize the cost estimation agent
        
        Args:
            region: AWS region for AgentCore Code Interpreter
        """
        self.region = region
        if not self.region:
            # Follow AWS SDK resolution order: AWS_DEFAULT_REGION > AWS_REGION > boto3 session
            self.region = os.environ.get('AWS_DEFAULT_REGION') or os.environ.get('AWS_REGION') or boto3.Session().region_name
        self.code_interpreter = None
        
        logger.info(f"Initializing AWS Cost Estimator Agent in region: {region}")
        
    def _setup_code_interpreter(self) -> None:
        """
        [AgentCore Code Interpreter] 세션 시작
        - KEY POINT: 각 요청마다 새 세션을 시작하고, 완료 후 반드시 stop() 호출
        - 보안 샌드박스에서 임의의 Python 코드를 실행할 수 있는 격리 환경
        """
        try:
            logger.info("Setting up AgentCore Code Interpreter...")
            self.code_interpreter = CodeInterpreter(self.region)
            # TODO 15: Code Interpreter 세션을 시작하는 메서드 호출
            # HINT: CodeInterpreter 클라이언트의 세션 시작 메서드입니다. 완료 후 반드시 stop()을 호출해야 합니다.
            # 참고: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-start-session.html
            self.code_interpreter."_____"()
            logger.info("✅ AgentCore Code Interpreter session started successfully")
        except Exception as e:
            logger.error(f"❌ Failed to setup Code Interpreter: {e}")
            return  # Handle the error instead of re-raising
    
    def _get_aws_credentials(self) -> dict:
        """
        Get current AWS credentials (including session token if present)
        
        Returns:
            Dict with current AWS credentials including session token
        """
        try:
            logger.info("Getting current AWS credentials...")
            
            # Create session to get current credentials
            session = boto3.Session()
            credentials = session.get_credentials()
            
            if credentials is None:
                raise Exception("No AWS credentials found")
            
            # Verify credentials work by getting caller identity
            sts_client = boto3.client('sts', region_name=self.region)
            identity = sts_client.get_caller_identity()
            logger.info(f"Using AWS identity: {identity.get('Arn', 'Unknown')}")
            
            # Get frozen credentials to access them
            frozen_creds = credentials.get_frozen_credentials()
            
            credential_dict = {
                "AWS_ACCESS_KEY_ID": frozen_creds.access_key,
                "AWS_SECRET_ACCESS_KEY": frozen_creds.secret_key,
                "AWS_REGION": self.region
            }
            
            # Add session token if available (EC2 instance role provides this)
            if frozen_creds.token:
                credential_dict["AWS_SESSION_TOKEN"] = frozen_creds.token
                logger.info("✅ Using AWS credentials with session token (likely from EC2 instance role)")
            else:
                logger.info("✅ Using AWS credentials without session token")
                
            return credential_dict
            
        except Exception as e:
            logger.error(f"❌ Failed to get AWS credentials: {e}")
            return {}  # Return empty dict as fallback

    def _setup_aws_pricing_client(self) -> MCPClient:
        """
        [AWS Pricing MCP Server] 가격 조회 도구 초기화
        - KEY POINT: uvx로 awslabs.aws-pricing-mcp-server를 subprocess로 실행
        - stdio 기반 MCP 통신 (stdin/stdout으로 JSON-RPC)
        - AWS 자격증명을 env로 전달하여 Pricing API 접근
        """
        try:
            logger.info("Setting up AWS Pricing MCP Client...")
            
            # Get current credentials (including session token if available)
            aws_credentials = self._get_aws_credentials()
            
            # Prepare environment variables for MCP client
            env_vars = {
                "FASTMCP_LOG_LEVEL": "ERROR",
                **aws_credentials  # Include all AWS credentials
            }
            
            # Find uvx binary: check PATH first, then fall back to uv package's bin dir
            # (in Runtime, /var/task/bin/ is not on PATH so shutil.which may fail)
            uvx_path = shutil.which("uvx")
            if not uvx_path:
                from uv._find_uv import find_uv_bin
                uv_bin = find_uv_bin()
                uvx_path = os.path.join(os.path.dirname(uv_bin), "uvx")

            aws_pricing_client = MCPClient(
                lambda: stdio_client(StdioServerParameters(
                    command=uvx_path,
                    # TODO 7: AWS Pricing MCP Server 패키지 이름 지정
                    # HINT: awslabs가 제공하는 AWS Pricing MCP 서버 패키지입니다.
                    # 참고: https://awslabs.github.io/mcp/servers/aws-pricing-mcp-server/
                    args=["_____"],
                    env=env_vars
                ))
            )
            logger.info("✅ AWS Pricing MCP Client setup successfully with AWS credentials")
            return aws_pricing_client
        except Exception as e:
            logger.error(f"❌ Failed to setup AWS Pricing MCP Client: {e}")
            return None  # Return None as fallback
    
    
    @tool
    def execute_cost_calculation(self, calculation_code: str, description: str = "") -> str:
        """
        [AgentCore Code Interpreter] 비용 계산 코드 실행
        
        - LLM이 생성한 Python 코드를 보안 샌드박스에서 실행
        - KEY POINT: 이 도구는 에이전트가 자율적으로 호출함 (사용자가 직접 호출 X)
        - 에이전트는 가격 데이터를 조회한 후, 계산 코드를 생성하여 이 도구에 전달
        
        Args:
            calculation_code: 실행할 Python 코드
            description: 계산 설명 (로깅용)
        """
        if not self.code_interpreter:
            return "❌ Code Interpreter not initialized"
            
        try:
            logger.info(f"🧮 Executing calculation: {description}")
            logger.debug(f"Code to execute:\n{calculation_code}")
            
            # TODO 6: Code Interpreter에서 코드를 실행하는 API 호출
            # HINT: CodeInterpreter 클라이언트의 invoke 메서드에 액션 이름과 파라미터를 전달합니다.
            # 참고: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-execute-code.html
            response = self.code_interpreter.invoke("_____", {
                "language": "_____",
                "code": calculation_code
            })
            
            # Extract results from response stream
            results = []
            for event in response.get("stream", []):
                if "result" in event:
                    result = event["result"]
                    if "content" in result:
                        for content_item in result["content"]:
                            if content_item.get("type") == "text":
                                results.append(content_item["text"])
            
            result_text = "\n".join(results)
            logger.info("✅ Calculation completed successfully")
            logger.debug(f"Calculation result: {result_text}")
            
            return result_text
            
        except Exception as e:
            logger.exception(f"❌ Calculation failed: {e}")

    @contextmanager
    def _estimation_agent(self) -> Generator[Agent, None, None]:
        """
        [핵심] 에이전트 생성 컨텍스트 매니저
        
        - Code Interpreter + Pricing MCP를 설정하고 Strands Agent를 생성
        - KEY POINT: 컨텍스트 매니저로 리소스 라이프사이클 관리
          → with 블록 끝나면 자동으로 Code Interpreter stop + MCP 연결 해제
        - 이 패턴은 AWS 리소스 누수를 방지하는 핵심 설계
        """        
        try:
            logger.info("🚀 Initializing AWS Cost Estimation Agent...")
            
            # Setup components in order
            self._setup_code_interpreter()
            aws_pricing_client = self._setup_aws_pricing_client()
            
            # Create agent with persistent MCP context
            with aws_pricing_client:
                pricing_tools = aws_pricing_client.list_tools_sync()
                logger.info(f"Found {len(pricing_tools)} AWS pricing tools")
                
                # TODO 8: Strands Agent 생성 시 도구 목록 전달
                # HINT: Agent에 사용할 도구(tool) 리스트를 전달합니다. 로컬 @tool 함수와 MCP 도구를 합칩니다.
                # 참고: https://strandsagents.com/latest/user-guide/concepts/tools/mcp/
                all_tools = [self.execute_cost_calculation] + pricing_tools
                agent = Agent(
                    BedrockModel(
                        boto_client_config=Config(
                            read_timeout=900,
                            connect_timeout=900,
                            retries=dict(max_attempts=3, mode="adaptive"),
                        ),
                        model_id=DEFAULT_MODEL
                    ),
                    "_____"=all_tools,
                    system_prompt=SYSTEM_PROMPT
                )
                
                yield agent
                
        except Exception as e:
            logger.exception(f"❌ Component setup failed: {e}")
            raise
        finally:
            # Ensure cleanup happens regardless of success/failure
            self.cleanup()

    def estimate_costs(self, architecture_description: str) -> str:
        """
        Estimate costs for a given architecture description
        
        Args:
            architecture_description: Description of the system to estimate
            
        Returns:
            Cost estimation results as concatenated string
        """
        logger.info("📊 Starting cost estimation...")
        logger.info(f"Architecture: {architecture_description}")
        
        try:
            with self._estimation_agent() as agent:
                # Use the agent to process the cost estimation request
                prompt = COST_ESTIMATION_PROMPT.format(
                    architecture_description=architecture_description
                )
                result = agent(prompt)
                
                logger.info("✅ Cost estimation completed")

                if result.message and result.message.get("content"):
                    # Extract text from all ContentBlocks and concatenate
                    text_parts = []
                    for content_block in result.message["content"]:
                        if isinstance(content_block, dict) and "text" in content_block:
                            text_parts.append(content_block["text"])
                    return "".join(text_parts) if text_parts else "No text content found."
                else:
                    return "No estimation result."

        except Exception as e:
            logger.exception(f"❌ Cost estimation failed: {e}")
            error_details = traceback.format_exc()
            return f"❌ Cost estimation failed: {e}\n\nStacktrace:\n{error_details}"

    def estimate_costs_with_gateway(self, architecture_description: str, gateway_mcp_client) -> str:
        """
        [AgentCore Gateway 통합] Gateway 도구 포함 견적
        
        표준 도구(Code Interpreter + Pricing)에 Gateway MCP 도구를 추가.
        에이전트가 markdown_to_email 등 외부 Lambda 도구를 사용할 수 있음.
        
        - KEY POINT: Gateway 도구는 with 블록 안에서만 유효
          → gateway_mcp_client가 컨텍스트 매니저로 MCP 연결을 관리
        - 시스템 프롬프트에 이메일 기능 안내를 동적으로 추가
        """
        logger.info("📊 Starting cost estimation with Gateway tools...")
        logger.info(f"Architecture: {architecture_description}")
        
        try:
            logger.info("🚀 Initializing AWS Cost Estimation Agent with Gateway...")
            
            # Setup components
            self._setup_code_interpreter()
            aws_pricing_client = self._setup_aws_pricing_client()
            
            # Create agent with Pricing MCP + Gateway MCP + Code Interpreter
            with aws_pricing_client:
                pricing_tools = aws_pricing_client.list_tools_sync()
                logger.info(f"Found {len(pricing_tools)} AWS pricing tools")
                
                with gateway_mcp_client:
                    # Get Gateway tools (e.g. markdown_to_email)
                    gateway_tools = []
                    more_tools = True
                    pagination_token = None
                    while more_tools:
                        tmp_tools = gateway_mcp_client.list_tools_sync(pagination_token=pagination_token)
                        gateway_tools.extend(tmp_tools)
                        if tmp_tools.pagination_token is None:
                            more_tools = False
                        else:
                            pagination_token = tmp_tools.pagination_token

                    gateway_tool_names = [t.tool_name for t in gateway_tools]
                    logger.info(f"Found {len(gateway_tools)} Gateway tools: {gateway_tool_names}")
                    
                    # Combine all tools
                    all_tools = [self.execute_cost_calculation] + pricing_tools + gateway_tools
                    
                    # Enhanced system prompt with email capability
                    system_prompt = SYSTEM_PROMPT + """

ADDITIONAL CAPABILITY:
- You have access to `markdown_to_email` tool via AgentCore Gateway.
- If the user asks to send the estimate via email, use this tool.
- Pass the full markdown estimate as `markdown_text` and the email address as `email_address`.
- Only use this tool when explicitly asked to send an email.
"""
                    
                    agent = Agent(
                        BedrockModel(
                            boto_client_config=Config(
                                read_timeout=900,
                                connect_timeout=900,
                                retries=dict(max_attempts=3, mode="adaptive"),
                            ),
                            model_id=DEFAULT_MODEL
                        ),
                        tools=all_tools,
                        system_prompt=system_prompt
                    )
                    
                    prompt = COST_ESTIMATION_PROMPT.format(
                        architecture_description=architecture_description
                    )
                    result = agent(prompt)
                    
                    logger.info("✅ Cost estimation with Gateway completed")

                    if result.message and result.message.get("content"):
                        text_parts = []
                        for content_block in result.message["content"]:
                            if isinstance(content_block, dict) and "text" in content_block:
                                text_parts.append(content_block["text"])
                        return "".join(text_parts) if text_parts else "No text content found."
                    else:
                        return "No estimation result."

        except Exception as e:
            logger.exception(f"❌ Cost estimation with Gateway failed: {e}")
            error_details = traceback.format_exc()
            return f"❌ Cost estimation failed: {e}\n\nStacktrace:\n{error_details}"
        finally:
            self.cleanup()

    async def estimate_costs_stream(self, architecture_description: str) -> AsyncGenerator[dict, None]:
        """
        [스트리밍] delta 기반 스트리밍 응답
        
        - KEY POINT: Strands stream_async()는 전체 누적 텍스트를 반환하는 경우가 있음
          → 이전 청크와 비교하여 새로운 delta만 추출하여 yield
        - ContentBlockDeltaEvent 패턴 (Amazon Bedrock 모범 사례)
        """
        logger.info("📊 Starting streaming cost estimation...")
        logger.info(f"Architecture: {architecture_description}")
        
        try:
            with self._estimation_agent() as agent:
                # Use the agent to process the cost estimation request with streaming
                prompt = COST_ESTIMATION_PROMPT.format(
                    architecture_description=architecture_description
                )
                
                logger.info("🔄 Streaming cost estimation response...")
                
                # Implement proper delta handling to prevent duplicates
                # This follows Amazon Bedrock ContentBlockDeltaEvent pattern
                previous_output = ""
                
                agent_stream = agent.stream_async(prompt, callback_handler=null_callback_handler)
                
                async for event in agent_stream:
                    if "data" in event:
                        current_chunk = str(event["data"])
                        
                        # Handle delta calculation following Bedrock best practices
                        if current_chunk.startswith(previous_output):
                            # This is an incremental update - extract only the new part
                            delta_content = current_chunk[len(previous_output):]
                            if delta_content:  # Only yield if there's actually new content
                                previous_output = current_chunk
                                yield {"data": delta_content}
                        else:
                            # This is a completely new chunk or reset - yield as-is
                            previous_output = current_chunk
                            yield {"data": current_chunk}
                    else:
                        # Pass through non-data events (errors, metadata, etc.)
                        yield event
                
                logger.info("✅ Streaming cost estimation completed")

        except Exception as e:
            logger.exception(f"❌ Streaming cost estimation failed: {e}")
            # Yield error event in streaming format
            yield {
                "error": True,
                "data": f"❌ Streaming cost estimation failed: {e}\n\nStacktrace:\n{traceback.format_exc()}"
            }

    def cleanup(self) -> None:
        """
        [리소스 정리] Code Interpreter 세션 중지
        - KEY POINT: 반드시 호출해야 함. 미호출 시 세션이 리전에 남아 비용 발생
        - _estimation_agent()의 finally 블록에서 자동 호출됨
        """
        logger.info("🧹 Cleaning up resources...")
        
        if self.code_interpreter:
            try:
                self.code_interpreter.stop()
                logger.info("✅ Code Interpreter session stopped")
            except Exception as e:
                logger.warning(f"⚠️ Error stopping Code Interpreter: {e}")
            finally:
                self.code_interpreter = None
