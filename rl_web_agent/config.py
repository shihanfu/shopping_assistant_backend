from dataclasses import dataclass


@dataclass
class ProxyConfig:
    """Configuration for proxy settings"""

    server: str = "http://localhost:8080"
    enabled: bool = True


@dataclass
class BrowserConfig:
    """Configuration for browser settings"""

    headless: bool = False
    browser_type: str = "chromium"  # chromium, firefox, webkit
    viewport_width: int = 1920
    viewport_height: int = 1080
    user_agent: str | None = None
    extra_http_headers: dict[str, str] | None = None


@dataclass
class EnvironmentConfig:
    """Configuration for the web environment"""

    target_url: str = "http://metis.lti.cs.cmu.edu:7770"
    target_host_rewrite: str | None = "metis.lti.cs.cmu.edu:7770=10.58.210.60:80"
    init_script_path: str = "rl_web_agent/javascript/initscript.js"
    parser_script_path: str = "rl_web_agent/javascript/parser.js"


@dataclass
class WebAgentConfig:
    """Main configuration for the web agent"""

    proxy: ProxyConfig = ProxyConfig()
    browser: BrowserConfig = BrowserConfig()
    environment: EnvironmentConfig = EnvironmentConfig()

    # General settings
    debug: bool = False
    log_level: str = "INFO"
    timeout: int = 30000  # milliseconds
