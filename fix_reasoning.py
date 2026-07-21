path = r"E:\aProgramming_code\GetAJobProject\企业知识库搭建\app\rag\graph.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Fix 1: when storing assistant answer in history, strip reasoning_content from API if any
# The fix is to NOT pass reasoning_content back - we don't need thinking mode
# Actually the issue is reversing: DeepSeek gave us reasoning_content in response,
# and we stored it in history. Then next request, DeepSeek complains reasoning_content missing.
# Solution: strip reasoning_content from all history messages before sending

# Add a cleaning function before sending history to LLM
old_send = "    messages.append({\"role\": \"user\", \"content\": message})"
new_send = """    # Clean messages: strip reasoning_content (thinking mode not needed)
    def _clean_msg(m):
        if isinstance(m, dict):
            return {k: v for k, v in m.items() if k != "reasoning_content"}
        return m
    clean_history = [_clean_msg(m) for m in history]
    messages = [{"role": "system", "content": full_system_prompt}]
    if memory_ctx:
        messages.append({"role": "system", "content": memory_ctx})
    messages.extend([_clean_msg(m) for m in chat_history_prompt])
    messages.append({"role": "user", "content": message})"""

# But wait, the existing code around line ~335-345 uses chat_history_prompt from prompts.py
# Let me find the exact area

# Actually simpler: strip reasoning_content when loading history
old_load = """async def _get_history(session_id: str) -> list[dict]:
    return load_history(session_id)"""

new_load = """async def _get_history(session_id: str) -> list[dict]:
    data = load_history(session_id)
    # Strip reasoning_content (DeepSeek thinking mode artifact)
    for m in data:
        m.pop("reasoning_content", None)
    return data"""

content = content.replace(old_load, new_load)

# Also strip when saving assistant response
old_save_assistant = """    history.append({"role": "assistant", "content": answer, "tool_calls": tc_data})"""
new_save_assistant = """    msg = {"role": "assistant", "content": answer}
    if tc_data:
        msg["tool_calls"] = tc_data
    history.append(msg)"""

content = content.replace(old_save_assistant, new_save_assistant)

old_save_full = """        history.append({"role": "assistant", "content": full_answer, "tool_calls": tc_list})"""
new_save_full = """        msg = {"role": "assistant", "content": full_answer}
        if tc_list:
            msg["tool_calls"] = tc_list
        history.append(msg)"""

content = content.replace(old_save_full, new_save_full)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("OK")