"""
模拟 agent 调用 run_modeling_script：建一个 80x60x15 的长方体。
"""
import sys
sys.path.insert(0, ".")

from catia_copilot.ai.tools import tool_run_modeling_script
import json

SCRIPT = """
from catia_copilot.catia.modeling import *

def build():
    part = create_part("AI_Test_Box")
    sk = add_sketch(part, "xy")
    draw_rect(sk, 0, 0, 80, 60)
    pad = add_pad(part, sk, 15)
    update_part(part)
"""

result = tool_run_modeling_script(script=SCRIPT)
print(json.dumps(json.loads(result), indent=2, ensure_ascii=False))
