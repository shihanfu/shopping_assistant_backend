"""
System prompt for the shopping assistant conversation.
"""


#use system_prompt_baseline.txt
SYSTEM_PROMPT = open(__file__.replace("system_prompt.py", "system_prompt_with_state.txt"), "r").read()
#user sysyem_conidition.txt
#SYSTEM_PROMPT = open(__file__.replace("system_prompt.py", "system_prompt_contrastive.txt"), "r").read()