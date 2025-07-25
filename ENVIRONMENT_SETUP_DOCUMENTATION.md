# RL Web Agent Environment Setup: Comprehensive Technical Documentation

## Table of Contents

1. [Introduction](#introduction)
2. [Architecture Overview](#architecture-overview)
3. [Observation Space Definition](#observation-space-definition)
4. [Action Space Definition](#action-space-definition)
5. [Browser Environment Configuration](#browser-environment-configuration)
6. [Network Activity Tracking](#network-activity-tracking)
7. [Task Configuration and Evaluation](#task-configuration-and-evaluation)
8. [Container Management and Proxy Integration](#container-management-and-proxy-integration)
9. [Advanced Features and Implementation Details](#advanced-features-and-implementation-details)

---

## Introduction

The RL Web Agent is a sophisticated reinforcement learning environment built on top of Playwright that enables automated browser interactions through a WebArena-like framework. This document provides comprehensive technical documentation focusing on the observation space definition (primarily implemented in `parser.js`) and the action space definition (primarily implemented in `env.py`).

The environment is designed to provide a clean, consistent interface for training and evaluating web automation agents. It features semantic element identification, robust action execution, network activity tracking, and comprehensive observation generation.

### Key Design Principles

1. **Semantic Element Identification**: Every interactive element is assigned a unique semantic ID for reliable targeting
2. **Fail-Fast Architecture**: Missing elements or configuration errors cause immediate failures rather than silent failures
3. **Async-First Design**: All operations are asynchronous using Playwright's async API
4. **Observation-Action Loop**: Clean separation between observation generation and action execution
5. **Resource Sharing**: Shared Playwright instance across multiple environment instances for efficiency

---

## Architecture Overview

The RL Web Agent environment consists of several interconnected components:

### Core Components

```
┌─────────────────────────────────────────────────────────────────┐
│                    WebAgentEnv (env.py)                        │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │   Action Space  │  │  Configuration  │  │ Container Mgmt  │ │
│  │   - click()     │  │  - Browser      │  │  - Incus        │ │
│  │   - type()      │  │  - Proxy        │  │  - Health Check │ │
│  │   - select()    │  │  - Timeouts     │  │  - Cleanup      │ │
│  │   - navigate()  │  │  - Sites        │  │                 │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Browser (Playwright)                         │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │  initscript.js  │  │   parser.js     │  │   Page Context  │ │
│  │  - Hover Track  │  │  - DOM Strip    │  │  - Tabs         │ │
│  │  - Network      │  │  - Semantic ID  │  │  - Navigation   │ │
│  │    Activity     │  │  - Observation  │  │  - State        │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### JavaScript Layer Integration

The environment integrates two critical JavaScript components:

1. **`initscript.js`**: Injected on page load to track hover events and network activity
2. **`parser.js`**: Executed on-demand to generate structured observations from the DOM

---

## Observation Space Definition

The observation space is primarily defined by `parser.js`, a sophisticated DOM processing script that transforms raw HTML into structured, actionable observations.

### Core Observation Structure

Every observation returned by `env.observation()` contains the following structure:

```python
{
    "html": str,                    # Processed HTML with semantic IDs
    "clickable_elements": List[str], # List of clickable semantic IDs
    "hoverable_elements": List[str], # List of hoverable semantic IDs
    "input_elements": List[dict],    # List of input element details
    "select_elements": List[dict],   # List of select element details
    "tabs": List[dict],             # Browser tab information
    "model_answer": str | None,     # Model's final answer if terminated
    "score": float,                 # Task evaluation score (0.0-1.0)
    "terminated": bool,             # Whether task is complete
    "error": str | None             # Error message if action failed
}
```

### DOM Processing Pipeline

The `parser.js` script implements a sophisticated DOM processing pipeline:

#### 1. Element Filtering

```javascript
const BLACKLISTED_TAGS = new Set([
    'script', 'style', 'link', 'meta', 'noscript', 'template',
    'iframe', 'svg', 'canvas', 'picture', 'video', 'audio',
    'object', 'embed'
]);
```

Elements with blacklisted tags are completely removed from the observation to reduce noise and focus on interactive content.

#### 2. Visibility Detection

```javascript
const style = window.getComputedStyle(original);
const hidden = style.display === 'none' || style.visibility === 'hidden' ||
              parseFloat(style.opacity) === 0;
const zeroSize = original.offsetWidth === 0 && original.offsetHeight === 0;
if (hidden || zeroSize) return null;
```

Elements that are not visible to the user are filtered out, ensuring only actionable elements appear in observations.

#### 3. Semantic ID Generation

The semantic ID system is the cornerstone of reliable element interaction:

```javascript
const slug = (t) =>
    t.toLowerCase().replace(/\s+/g, ' ').trim()
        .replace(/[^\w]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 20);

const uniqueName = (base) => {
    let name = base || 'item';
    if (!USED_SEMANTIC_IDS.has(name)) {
        USED_SEMANTIC_IDS.add(name);
        return name;
    }
    let i = 1;
    while (USED_SEMANTIC_IDS.has(name + i)) i++;
    USED_SEMANTIC_IDS.add(name + i);
    return name + i;
};
```

**Semantic ID Generation Rules:**

1. **Text-based**: Primary ID derived from element's text content
2. **Attribute fallback**: Uses `title`, `placeholder`, or `name` attributes if no text
3. **Tag fallback**: Uses element tag name as last resort
4. **Hierarchical**: Child elements include parent's semantic ID as prefix
5. **Uniqueness**: Numeric suffixes ensure global uniqueness
6. **Length limit**: IDs truncated to 20 characters for manageable size

**Examples:**
- Button with text "Sign In" → `sign_in`
- Second "Sign In" button → `sign_in1`
- Input with placeholder "Enter password" → `enter_password`
- Nested element → `parent_element.child_button`

#### 4. Clickability Detection

The system uses sophisticated heuristics to identify clickable elements:

```javascript
const probablyClickable = (() => {
    if (['button', 'select', 'summary', 'area', 'input'].includes(tag)) return true;
    if (tag === 'a' && original.hasAttribute('href')) return true;
    if (original.hasAttribute('onclick')) return true;
    const r = original.getAttribute('role');
    if (r === 'button' || r === 'link') return true;
    return style.cursor === 'pointer';
})();

const isClickable = !parentIsClickable && probablyClickable && !isDisabled;
```

**Clickability Rules:**
- **Intrinsically clickable**: buttons, links, inputs, selects
- **Event handlers**: Elements with `onclick` attributes
- **ARIA roles**: Elements with `role="button"` or `role="link"`
- **CSS cursor**: Elements with `cursor: pointer`
- **Disabled elements**: Explicitly excluded from clickable set
- **Parent context**: Child elements of clickable parents are not independently clickable

#### 5. Input Element Processing

Input elements receive special processing to capture their state:

```javascript
if (tag === 'input' || tag === 'textarea' || original.hasAttribute('contenteditable')) {
    const inputIsDisabled = original.disabled || original.readOnly;

    if (!inputIsDisabled && thisName) {
        clone.setAttribute('data-semantic-id', thisName);
        clone.setAttribute('data-value', original.value || '');
        clone.setAttribute('data-input-disabled', 'false');
        clone.setAttribute('data-can-edit', !original.readOnly ? 'true' : 'false');

        // Numeric inputs get additional data
        if (t === 'number') {
            clone.setAttribute('data-numeric-value', original.valueAsNumber || '');
        }

        // Text selection state
        if (original.selectionStart !== undefined) {
            clone.setAttribute('data-selection-start', original.selectionStart);
            clone.setAttribute('data-selection-end', original.selectionEnd);
        }
    }
}
```

**Input Element Data Captured:**
- **Current value**: Text content or selected value
- **Edit capability**: Whether element is readonly
- **Numeric data**: Parsed numeric value for number inputs
- **Selection state**: Current text selection range
- **Focus state**: Whether element is currently focused

#### 6. Select Element Processing

Select (dropdown) elements receive comprehensive state capture:

```javascript
if (tag === 'select') {
    clone.setAttribute('data-value', original.value);
    clone.setAttribute('data-selected-index', original.selectedIndex);
    clone.setAttribute('data-has-multiple', original.multiple ? 'true' : 'false');

    const selectedOptions = Array.from(original.selectedOptions)
        .map(opt => opt.value)
        .join(',');
    clone.setAttribute('data-selected-values', selectedOptions);

    // Process individual options
    for (const opt of original.querySelectorAll('option')) {
        const o = document.createElement('option');
        o.textContent = opt.textContent.trim();
        o.setAttribute('value', opt.value);
        o.setAttribute('data-selected', opt.selected ? 'true' : 'false');
        const optName = uniqueName(`${thisName}.${slug(opt.textContent)}`);
        o.setAttribute('data-semantic-id', optName);
        clone.appendChild(o);
    }
}
```

**Select Element Data Captured:**
- **Current selection**: Selected value and index
- **Multiple selection**: Whether multi-select is enabled
- **All options**: Each option gets its own semantic ID
- **Option states**: Which options are currently selected

#### 7. DOM Structure Preservation

The parser maintains essential DOM hierarchy while stripping unnecessary content:

```javascript
// Preserve text nodes
for (const n of original.childNodes) {
    if (n.nodeType === 3 && n.textContent.trim()) {
        clone.appendChild(document.createTextNode(n.textContent.trim()));
    }
}

// Flatten nested divs
const flatten = (el) => {
    while (el.children.length === 1) {
        const child = el.children[0];
        const p = el.tagName.toLowerCase();
        const c = child.tagName.toLowerCase();
        if (p !== 'div' && c !== 'div') break;
        el = (p === 'div' && c !== 'div')
            ? replaceElement(el, child.tagName, child)
            : (pullUpChild(el, child), el);
    }
    return el;
};
```

### Observation Generation Process

The observation generation follows this sequence:

1. **Page Stability Wait**: Environment waits for DOM content loaded and network idle
2. **Parser Execution**: `parser.js` is executed in browser context
3. **Data Extraction**: Structured data is extracted from processed DOM
4. **Metadata Addition**: Tab information, evaluation scores, and error states are added
5. **Return**: Complete observation is returned to agent

---

## Action Space Definition

The action space is defined in `env.py` through the `step()` method and supporting action methods. All actions are specified as JSON strings for consistency and ease of parsing.

### Core Action Interface

```python
async def step(self, action: str) -> dict:
    """
    Execute an action using JSON string format.

    Args:
        action: JSON string describing the action

    Returns:
        dict: Next observation after executing action
    """
```

### Supported Action Types

#### 1. Click Actions

```python
# JSON Format
'{"action": "click", "target": "semantic_id"}'

# Implementation
async def click(self, semantic_id: str) -> None:
    selector = f'[data-semantic-id="{semantic_id}"]'
    element = self.page.locator(selector)
    await element.scroll_into_view_if_needed(timeout=500)
    await element.click(force=True)
```

**Click Action Features:**
- **Semantic targeting**: Uses semantic IDs from observation
- **Auto-scroll**: Elements are scrolled into view before clicking
- **Force click**: Bypasses some Playwright safety checks for automation
- **Fast timeout**: 500ms timeout to fail fast on non-existent elements

#### 2. Text Input Actions

```python
# JSON Format
'{"action": "type", "target": "semantic_id", "text": "content", "enter": true}'

# Implementation
async def type(self, semantic_id: str, text: str, press_enter: bool = False) -> None:
    selector = f'[data-semantic-id="{semantic_id}"]'
    element = self.page.locator(selector)
    await element.scroll_into_view_if_needed(timeout=500)
    await element.fill(text, force=True)  # Clear and type
    if press_enter:
        await element.press("Enter", force=True)
```

**Text Input Features:**
- **Clear-then-fill**: `fill()` clears existing content before typing
- **Optional Enter**: Can press Enter after typing
- **Force operation**: Bypasses readonly/disabled checks when needed

#### 3. Hover Actions

```python
# JSON Format
'{"action": "hover", "target": "semantic_id"}'

# Implementation
async def hover(self, semantic_id: str) -> None:
    selector = f'[data-semantic-id="{semantic_id}"]'
    element = self.page.locator(selector)
    await element.scroll_into_view_if_needed(timeout=500)
    await element.hover(force=True)
```

**Hover Action Features:**
- **Tooltip activation**: Triggers hover states for tooltips and dropdowns
- **Event simulation**: Generates proper mouse events
- **Integration**: Works with hover detection from `initscript.js`

#### 4. Select/Dropdown Actions

```python
# JSON Format
'{"action": "select", "target": "semantic_id", "value": "option_value"}'

# Implementation
async def select(self, semantic_id: str, value: str) -> None:
    selector = f'[data-semantic-id="{semantic_id}"]'
    element = self.page.locator(selector)
    await element.scroll_into_view_if_needed(timeout=500)
    await element.select_option(value, force=True)
```

#### 5. Element Clearing Actions

```python
# JSON Format
'{"action": "clear", "target": "semantic_id"}'

# Implementation
async def clear(self, semantic_id: str) -> None:
    selector = f'[data-semantic-id="{semantic_id}"]'
    element = self.page.locator(selector)
    await element.scroll_into_view_if_needed(timeout=500)
    await element.clear(force=True)
```

#### 6. Keyboard Actions

```python
# JSON Format - Global key press
'{"action": "key_press", "key": "Escape"}'

# JSON Format - Element-specific key press
'{"action": "key_press", "key": "Enter", "target": "semantic_id"}'

# Implementation
async def key_press(self, key: str, semantic_id: str | None = None) -> None:
    if semantic_id:
        selector = f'[data-semantic-id="{semantic_id}"]'
        element = self.page.locator(selector)
        await element.scroll_into_view_if_needed(timeout=500)
        await element.press(key, force=True)
    else:
        await self.page.keyboard.press(key)
```

**Supported Keys:**
- **Navigation**: `Tab`, `Shift+Tab`, `ArrowUp`, `ArrowDown`, `ArrowLeft`, `ArrowRight`
- **Action**: `Enter`, `Space`, `Escape`
- **Editing**: `Backspace`, `Delete`, `Home`, `End`
- **Modifiers**: `Ctrl+a`, `Ctrl+c`, `Ctrl+v`, etc.

#### 7. Navigation Actions

```python
# URL Navigation
'{"action": "goto_url", "url": "https://example.com"}'

# Browser History
'{"action": "back"}'
'{"action": "forward"}'
'{"action": "refresh"}'
```

**Navigation Implementation:**
```python
async def goto_url(self, url: str) -> None:
    await self.page.goto(url, wait_until="domcontentloaded")

async def back(self) -> None:
    await self.page.go_back(wait_until="domcontentloaded")

async def forward(self) -> None:
    await self.page.go_forward(wait_until="domcontentloaded")

async def refresh(self) -> None:
    await self.page.reload(wait_until="domcontentloaded")
```

#### 8. Tab Management Actions

```python
# Create new tab
'{"action": "new_tab", "url": "https://example.com"}'  # URL optional

# Switch to existing tab
'{"action": "switch_tab", "tab_id": 1}'

# Close tab
'{"action": "close_tab", "tab_id": 1}'
```

**Tab Management Implementation:**
```python
async def new_tab(self, url: str | None = None) -> int:
    page = await self.context.new_page()
    if url:
        await page.goto(url, wait_until="domcontentloaded")
    self.page = page  # Make new tab active
    return len(self.context.pages) - 1

async def switch_tab(self, tab_id: int) -> None:
    if 0 <= tab_id < len(self.context.pages):
        self.page = self.context.pages[tab_id]
        await self.page.bring_to_front()
    else:
        raise ValueError(f"Invalid tab ID: {tab_id}")
```

#### 9. Task Termination

```python
# JSON Format
'{"action": "terminate", "answer": "The product costs $29.99"}'

# Implementation
async def terminate(self, answer: str = "") -> None:
    self.model_answer = answer
    self.logger.info(f"Task terminated with answer: {answer}")
```

### Action Execution Flow

1. **JSON Parsing**: Action string is parsed into structured data
2. **Validation**: Required parameters are validated
3. **Element Location**: Target elements are located using semantic IDs
4. **Scroll**: Elements are scrolled into view if needed
5. **Execution**: Action is performed with force flag for reliability
6. **Sleep**: Optional sleep after action for page stability
7. **Observation**: New observation is generated and returned
8. **Error Handling**: Errors are caught and included in observation

### Error Handling Strategy

The action system implements a fail-fast approach:

```python
try:
    action_data = json.loads(action)
    # Execute action...
    observation = await self.observation()
    observation["error"] = None
    return observation
except json.JSONDecodeError as e:
    observation = await self.observation()
    observation["error"] = f"Invalid JSON action format: {e}"
    return observation
except Exception as e:
    observation = await self.observation()
    observation["error"] = f"Error executing action: {e}"
    return observation
```

**Error Types:**
- **JSON Parse Errors**: Invalid action format
- **Missing Parameters**: Required action parameters not provided
- **Element Not Found**: Semantic ID doesn't match any element
- **Playwright Errors**: Browser-level execution failures
- **Timeout Errors**: Actions taking too long to complete

---

## Browser Environment Configuration

The browser environment is extensively configurable through the `config.yaml` file and supports both regular and persistent browser contexts.

### Browser Launch Configuration

```yaml
browser:
  launch_options:
    headless: false                  # Visual browser for debugging
    args:                           # Performance optimization
      - "--disable-autofill"
      - "--disable-extensions"
      - "--disable-background-networking"
      - "--no-first-run"
      - "--no-default-browser-check"
      - "--disable-sync"
      - "--disable-translate"
```

### Context Configuration

```yaml
context_options:
  viewport:
    width: 1920                    # Standard desktop resolution
    height: 1080
  user_agent: null                 # Default browser user agent
  extra_http_headers: null         # Set dynamically for proxy integration
```

### Persistent Browser Sessions

The environment supports persistent browser sessions for maintaining state across runs:

```python
if user_data_dir:
    # Use launch_persistent_context for user data directory
    persistent_options = {**launch_options, **context_options}
    self.context = await self.context_manager.chromium.launch_persistent_context(
        user_data_dir, **persistent_options)
    self.browser = self.context.browser
else:
    # Regular launch without persistent context
    self.browser = await self.context_manager.chromium.launch(**launch_options)
    self.context = await self.browser.new_context(**context_options)
```

**Benefits of Persistent Sessions:**
- **Cookie persistence**: Login sessions maintained across runs
- **Cache efficiency**: Network cache preserved for faster loading
- **User preferences**: Browser settings and extensions persist
- **Reduced setup time**: Skip repeated authentication flows

### Timeout Configuration

The environment uses multiple timeout configurations for different scenarios:

```yaml
timeouts:
  default: 5000                    # Element interaction timeout
  page_load_domcontent: 10000     # DOM content loaded timeout
  page_load_networkidle: 30000    # Network idle timeout
  element_wait: 5000              # Element existence timeout
  custom_network_idle: 3000       # Custom network activity timeout
  container_health_check: 30000   # Container startup timeout
```

### Shared Playwright Instance

The environment implements a shared Playwright instance pattern for efficiency:

```python
class WebAgentEnv:
    _shared_playwright: ClassVar[Playwright | None] = None
    _shared_playwright_users: ClassVar[int] = 0

    @classmethod
    async def _ensure_playwright(cls) -> Playwright:
        if cls._shared_playwright is None:
            cls._shared_playwright = await async_playwright().start()
        cls._shared_playwright_users += 1
        return cls._shared_playwright

    @classmethod
    async def _cleanup_playwright(cls) -> None:
        cls._shared_playwright_users -= 1
        if cls._shared_playwright_users == 0 and cls._shared_playwright is not None:
            await cls._shared_playwright.stop()
            cls._shared_playwright = None
```

**Benefits:**
- **Resource efficiency**: Single Playwright instance serves multiple environments
- **Faster startup**: Subsequent environments launch faster
- **Memory optimization**: Reduced memory overhead for parallel execution
- **Process management**: Automatic cleanup when all environments close

---

## Network Activity Tracking

The environment includes sophisticated network activity tracking through `initscript.js` to provide better observation timing.

### Network Activity Monitor

```javascript
window.__networkActivity = {
    activeRequests: 0,
    lastActivity: Date.now(),
    eventTarget: new EventTarget(),

    // Track XHR requests
    trackXHR: function() {
        const originalXHR = window.XMLHttpRequest;
        window.XMLHttpRequest = function() {
            const xhr = new originalXHR();

            const originalOpen = xhr.open;
            xhr.open = function() {
                window.__networkActivity.activeRequests++;
                window.__networkActivity.lastActivity = Date.now();
                return originalOpen.apply(this, arguments);
            };

            const originalSend = xhr.send;
            xhr.send = function() {
                const onComplete = () => {
                    window.__networkActivity.activeRequests--;
                    window.__networkActivity.lastActivity = Date.now();
                };

                xhr.addEventListener('load', onComplete);
                xhr.addEventListener('error', onComplete);
                xhr.addEventListener('abort', onComplete);

                return originalSend.apply(this, arguments);
            };

            return xhr;
        };
    },

    // Track fetch requests
    trackFetch: function() {
        const originalFetch = window.fetch;
        window.fetch = function() {
            window.__networkActivity.activeRequests++;
            window.__networkActivity.lastActivity = Date.now();

            return originalFetch.apply(this, arguments).finally(() => {
                window.__networkActivity.activeRequests--;
                window.__networkActivity.lastActivity = Date.now();
            });
        };
    }
};
```

### Network Idle Detection

The system provides both synchronous and asynchronous network idle detection:

```javascript
// Synchronous check
isIdle: function(idleTimeMs = 500) {
    const now = Date.now();
    return this.activeRequests === 0 &&
           (now - this.lastActivity) >= idleTimeMs;
},

// Asynchronous wait with timeout
waitForIdle: function(idleTimeMs = 500, timeoutMs = 10000) {
    return new Promise((resolve) => {
        // Complex logic for waiting with event listeners and timeouts
        // ... (detailed implementation in initscript.js)
    });
}
```

### Observation Timing Integration

The network tracking integrates with observation generation:

```python
async def observation(self):
    # Wait for Playwright's networkidle (handles page loads)
    try:
        await self.page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        pass  # Timeout is normal for some pages

    # Wait for custom network idle detection (handles XHR/fetch)
    await self._wait_for_custom_network_idle(
        timeout_ms=30000,
        idle_time_ms=3000
    )
```

**Network Idle Detection Benefits:**
- **Better observations**: Waits for AJAX content to load
- **Reduced noise**: Avoids capturing intermediate loading states
- **Reliability**: Ensures page is stable before observation
- **Timeout protection**: Prevents infinite waiting on broken pages

### Hover Event Detection

The `initscript.js` also tracks hover event listeners for better clickability detection:

```javascript
const ORIG = EventTarget.prototype.addEventListener;
const HOVER = new Set(['mouseenter', 'mouseover', 'pointerenter']);

EventTarget.prototype.addEventListener = function (type, listener, opts) {
    if (HOVER.has(type)) {
        if (this && this.setAttribute) {
            this.setAttribute('data-maybe-hoverable', 'true');
        }
    }
    return ORIG.call(this, type, listener, opts);
};
```

This allows the parser to identify elements that may respond to hover actions even if they don't have obvious visual hover indicators.

---

## Task Configuration and Evaluation

The environment supports comprehensive task configuration and evaluation through JSON task configs and built-in evaluation logic.

### Task Configuration Structure

```python
task_config = {
    "task_id": 1,
    "intent": "Navigate to shopping site and find product price",
    "start_url": "http://shopping.example.com",
    "sites": ["shopping"],  # Required sites to launch
    "eval": {
        "eval_types": ["string_match"],
        "reference_answers": ["$29.99"],
        "string_note": "The exact price should be extracted"
    }
}
```

### Container Management Integration

When a task specifies required sites, the environment automatically manages Docker containers:

```python
async def setup(self, task_config: dict | None = None):
    if self.task_config and "sites" in self.task_config:
        # Launch containers for each required site
        for site in self.task_config["sites"]:
            try:
                container_name = f"{site}-{self.uuid}"
                ip_address = await launch_container(
                    incus_server_url,
                    base_container_name,
                    container_name,
                    proxy_server=proxy_server
                )
                self.server_ips[site] = ip_address
                self.launched_containers.append(container_name)
            except Exception as e:
                # Fallback to placeholder IP
                self.server_ips[site] = "10.2.1.203"
```

### Container Health Checking

The environment includes robust health checking for launched containers:

```python
async def _wait_for_containers_online(self) -> None:
    async with httpx.AsyncClient(timeout=10.0, proxy=proxy) as client:
        while pending_sites and (time_elapsed < timeout_seconds):
            for site_name, ip_address in pending_sites.items():
                try:
                    health_url = f"http://{ip_address}:80"
                    response = await client.head(health_url)
                    if response.status_code < 400:
                        self.logger.info(f"✅ {site_name} is now online")
                        sites_to_remove.append(site_name)
                except (httpx.TimeoutException, httpx.ConnectError):
                    # Expected during startup
                    continue
```

### Authentication Management

The environment supports automatic authentication for configured sites:

```python
async def login_to_site(self, site_name: str) -> None:
    if site_name == "shopping":
        await login_page.goto(login_url)
        await login_page.get_by_label("Email", exact=True).fill(username)
        await login_page.get_by_label("Password", exact=True).fill(password)
        await login_page.get_by_role("button", name="Sign In").click()
        await login_page.wait_for_load_state("networkidle")
    elif site_name == "reddit":
        # Site-specific login logic...
```

### Task Evaluation

The environment includes built-in task evaluation:

```python
async def evaluate_task(self) -> float:
    from rl_web_agent.evaluator import evaluate_task

    evaluation_context = {
        "task_config": self.task_config,
        "env_config": self.config,
        "extra_headers": self.extra_headers,
    }
    score = await evaluate_task(
        answer=self.model_answer or "",
        page=self.page,
        config=evaluation_context
    )
    return score
```

**Evaluation Integration:**
- **Automatic scoring**: Tasks are automatically evaluated when terminated
- **Multiple eval types**: String matching, page content analysis, etc.
- **Score normalization**: All scores normalized to 0.0-1.0 range
- **Context awareness**: Evaluation has access to page content and configuration

---

## Container Management and Proxy Integration

The environment includes sophisticated container orchestration and proxy integration for realistic web testing scenarios.

### Incus Container Integration

The environment uses Incus (LXC) containers to provide isolated web services:

```python
# Container lifecycle management
class WebAgentEnv:
    def __init__(self, environment_config: DictConfig):
        self.launched_containers: list[str] = []  # Track launched containers
        self.server_ips: dict[str, str] = {}      # Site name to IP mapping
```

### Container Launch Process

```python
async def setup(self, task_config: dict | None = None):
    if self.task_config and "sites" in self.task_config:
        # Check Incus server availability
        if not await health_check(incus_server_url, proxy_server=proxy_server):
            # Fallback to placeholder IPs
            for site in self.task_config["sites"]:
                self.server_ips[site] = "10.2.1.203"
        else:
            # Launch containers for each required site
            for site in self.task_config["sites"]:
                container_name = f"{site}-{self.uuid}"
                ip_address = await launch_container(
                    incus_server_url,
                    base_container_name,
                    container_name,
                    proxy_server=proxy_server
                )
                self.server_ips[site] = ip_address
                self.launched_containers.append(container_name)
```

### Proxy Configuration and Host Rewriting

The environment integrates with an HTTP proxy for host rewriting:

```python
# Configure proxy for browser
if self.config.proxy.enabled:
    launch_options["proxy"] = {"server": self.config.proxy.server}

# Set up host rewrite headers
extra_headers = {}
rewrite_mappings = []
for site_name, hostname in self.config.sites.items():
    if site_name in self.server_ips:
        server_ip = self.server_ips[site_name]
        rewrite_mapping = f"{hostname}={server_ip}:80"
        rewrite_mappings.append(rewrite_mapping)

if rewrite_mappings:
    extra_headers["x-target-host-rewrite"] = rewrite_mappings[0]
    context_options["extra_http_headers"] = extra_headers
```

**Proxy Integration Benefits:**
- **Host rewriting**: Dynamic mapping of hostnames to container IPs
- **Load balancing**: Distribute requests across multiple container instances
- **SSL termination**: Handle HTTPS termination at proxy level
- **Request logging**: Centralized logging of all HTTP traffic
- **Authentication**: Proxy-level authentication for AWS API Gateway integration

### Container Cleanup

The environment implements comprehensive cleanup for launched containers:

```python
async def close(self):
    if self.launched_containers:
        # Delete containers in parallel for faster cleanup
        deletion_tasks = []
        for container_name in self.launched_containers:
            task = asyncio.create_task(
                self._delete_container_with_retry(
                    incus_server_url,
                    container_name,
                    proxy_server
                )
            )
            deletion_tasks.append(task)

        if deletion_tasks:
            results = await asyncio.gather(*deletion_tasks, return_exceptions=True)
            success_count = sum(1 for result in results if result is True)
            self.logger.info(f"✅ Successfully cleaned up {success_count} containers")
```

### Container Retry Logic

```python
async def _delete_container_with_retry(
    self,
    incus_server_url: str,
    container_name: str,
    proxy_server: str | None,
    max_retries: int = 2
) -> bool:
    for attempt in range(max_retries + 1):
        try:
            await delete_container(incus_server_url, container_name, proxy_server)
            return True
        except Exception as e:
            if attempt < max_retries:
                await asyncio.sleep(1)  # Wait before retry
            else:
                self.logger.error(f"❌ Final failure deleting {container_name}: {e}")
                return False
```

---

## Advanced Features and Implementation Details

### Tracing and Debugging

The environment supports Playwright tracing for comprehensive debugging:

```python
async def _setup_tracing(self) -> None:
    if not self.config.tracing.enabled:
        return

    self.trace_file_path = self.config.tracing.output_path
    await self.context.tracing.start(
        screenshots=self.config.tracing.get("screenshots", True),
        snapshots=self.config.tracing.get("snapshots", True),
        sources=self.config.tracing.get("sources", True),
    )
```

**Trace Features:**
- **Screenshots**: Visual recording of every action
- **DOM snapshots**: Complete page state at each step
- **Network requests**: All network traffic during execution
- **Console logs**: Browser console output
- **Timeline**: Precise timing of all events

### Multi-Tab Support

The environment provides comprehensive multi-tab management:

```python
async def _get_tabs_info(self) -> list[dict]:
    tabs_info = []
    for i, page in enumerate(self.context.pages):
        tabs_info.append({
            "id": i,
            "title": await page.title(),
            "url": page.url,
            "is_active": page == self.page
        })
    return tabs_info
```

**Tab Management Features:**
- **Tab creation**: Create new tabs with optional URL navigation
- **Tab switching**: Switch between tabs with focus management
- **Tab closing**: Close tabs with automatic active tab management
- **Tab information**: Complete metadata about all open tabs

### Performance Optimizations

The environment includes several performance optimizations:

#### 1. Browser Argument Optimization

```python
args: [
    "--disable-autofill",
    "--disable-extensions",
    "--disable-background-networking",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-sync",
    "--disable-translate"
]
```

#### 2. Image Blocking

```python
# Block images to speed up page loads (not shown in config but commonly used)
await context.route("**/*.{png,jpg,jpeg,gif,webp,svg}", lambda route: route.abort())
```

#### 3. Cache Management

```python
# Persistent disk cache for faster repeated loads
cache_dir = Path(self.config.browser.cache_dir).resolve()
cache_dir.mkdir(parents=True, exist_ok=True)
cache_arg = f"--disk-cache-dir={cache_dir}"
launch_options["args"] = launch_options.get("args", []) + [cache_arg]
```

#### 4. Parallel Operations

```python
# Parallel container deletion for faster cleanup
deletion_tasks = []
for container_name in self.launched_containers:
    task = asyncio.create_task(self._delete_container_with_retry(...))
    deletion_tasks.append(task)

results = await asyncio.gather(*deletion_tasks, return_exceptions=True)
```

### Error Handling and Resilience

The environment implements comprehensive error handling:

#### 1. Graceful Degradation

```python
# Fallback to placeholder IPs if container launch fails
try:
    ip_address = await launch_container(...)
    self.server_ips[site] = ip_address
except Exception as e:
    self.logger.error(f"Failed to launch container for site {site}: {e}")
    self.server_ips[site] = "10.2.1.203"  # Fallback IP
```

#### 2. Timeout Protection

```python
# Multiple timeout layers
await element.scroll_into_view_if_needed(timeout=500)  # Fast fail for bad elements
await self.page.wait_for_load_state("domcontentloaded", timeout=10000)  # Page loads
await self._wait_for_custom_network_idle(timeout_ms=30000)  # Network activity
```

#### 3. Resource Cleanup

```python
async def close(self):
    # Stop tracing
    await self._stop_tracing()

    # Clean up containers
    await self._cleanup_containers()

    # Stop Playwright
    if self.context_manager:
        await self._cleanup_playwright()
```

### Configuration Management

The environment uses Hydra for sophisticated configuration management:

```python
# Single config file approach
# rl_web_agent/conf/config.yaml contains all settings

# Command-line overrides
# python -m rl_web_agent.main environment.browser.headless=true

# Environment variable resolution
api_key: "${oc.env:OPENAI_API_KEY}"

# Timestamped output directories
hydra:
  run:
    dir: ./outputs/${now:%Y-%m-%d_%H-%M-%S}
```

**Configuration Benefits:**
- **Single source of truth**: All configuration in one file
- **Runtime overrides**: Change any setting from command line
- **Environment integration**: Automatic environment variable resolution
- **Timestamped outputs**: Organized output directories for each run
- **Type safety**: Configuration validation through OmegaConf

### Extensibility and Customization

The environment is designed for easy extension:

#### 1. Custom Actions

```python
# Add new action types to step() method
elif action_name == "custom_action":
    await self.custom_action_handler(action_data)
```

#### 2. Custom Observations

```python
# Extend observation() method
async def observation(self):
    content = await super().observation()
    content["custom_data"] = await self.get_custom_data()
    return content
```

#### 3. Custom Evaluation

```python
# Implement custom evaluators
from rl_web_agent.evaluator import evaluate_task

async def evaluate_task(answer: str, page: Page, config: dict) -> float:
    # Custom evaluation logic
    return score
```

#### 4. Site-Specific Logic

```python
# Add site-specific login flows
elif site_name == "custom_site":
    await login_page.goto(f"http://{self.config.sites[site_name]}/login")
    # Custom login logic...
```

### Integration Examples

#### 1. OpenAI Integration

```python
# Configure OpenAI LLM provider
llm:
  provider: "openai"
  openai:
    api_key: "${oc.env:OPENAI_API_KEY}"
    model: "gpt-4o"
    max_tokens: 2000
```

#### 2. AWS Bedrock Integration

```python
# Configure Bedrock LLM provider
llm:
  provider: "bedrock"
  bedrock:
    region: "us-east-1"
    model_id: "anthropic.claude-sonnet-4"
```

#### 3. Custom Proxy Integration

```python
# Configure custom proxy with authentication
proxy:
  server: "http://proxy.company.com:8080"
  enabled: true
  # Additional proxy settings can be added
```

This comprehensive documentation covers the complete environment setup, focusing on the observation space definition implemented in `parser.js` and the action space definition implemented in `env.py`. The environment provides a robust, scalable foundation for training and evaluating web automation agents with sophisticated state representation and reliable action execution.
