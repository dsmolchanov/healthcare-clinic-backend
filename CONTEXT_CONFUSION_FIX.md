# Context Confusion Fix

## Problem

The AI agent gets confused between the user's current request and past/other potential appointments, creating a frustrating user experience.

### Example Issue

1. User asks about **veneers**
2. Agent's response brings up an unrelated appointment with **"Dr. Dan for a filling"**
3. This continues for several turns, with the agent trying to manage two appointments at once
4. Even after user explicitly states "Мне не нужен дан с пломбой" ("I don't need Dan for a filling"), the agent still mixes contexts

### Root Causes

1. **Poor State Management**: System retrieves context about a previously booked appointment and mixes it with the new, unrelated request
2. **Over-Injection of Context**: Warning `⚠️ Injecting pending action context` shows flawed rule that forces old context into the prompt, confusing the LLM
3. **No Relevance Checking**: System injects pending action context WITHOUT checking if the user's new message is actually related

---

## Solution

We've implemented a three-part solution:

### 1. 🎯 Context Relevance Checker

**File**: `app/services/context_relevance_checker.py` (NEW)

Determines if pending action context is relevant to the user's current message BEFORE injecting it.

#### Features:
- **Fast heuristic checks**: Detects explicit negation keywords
  - Russian: 'не нужен', 'не надо', 'не хочу', 'другое', 'забудь'
  - Spanish: 'no necesito', 'no quiero', 'otro', 'diferente'
  - English: 'don't need', 'don't want', 'something else', 'forget'

- **Semantic similarity**: Uses LLM to check if new message relates to pending action
  ```python
  is_relevant, confidence, reasoning = await checker.is_context_relevant(
      current_message="Хочу узнать про виниры",  # "Want to know about veneers"
      pending_action="checking availability for Dr. Dan for a filling",
      conversation_history=recent_messages
  )
  # Returns: (False, 0.0, "User asking about completely different service")
  ```

- **Intent extraction**: Identifies if user is starting a new request
  ```python
  intent = await checker.extract_current_intent(
      message="Хочу записаться на виниры",
      language='ru'
  )
  # Returns: {"intent": "service_inquiry", "is_new_request": True}
  ```

### 2. 🔄 Conversation State Machine

**File**: `app/services/conversation_state_machine.py` (NEW)

Explicitly manages conversation state to prevent context bleeding.

#### States:
```python
class ConversationState(Enum):
    IDLE = "idle"                           # No active task
    SERVICE_INQUIRY = "service_inquiry"     # Asking about services
    BOOKING_NEW = "booking_new"             # Creating new appointment
    BOOKING_CONFIRMATION = "booking_confirmation"  # Confirming details
    RESCHEDULING = "rescheduling"           # Modifying appointment
    CANCELING = "canceling"                 # Canceling appointment
    AWAITING_INFO = "awaiting_info"         # Waiting for user info
    ESCALATED = "escalated"                 # Handed to human
```

#### Context Tracking:
```python
@dataclass
class StateContext:
    service_id: Optional[str]
    service_name: Optional[str]     # e.g., "veneers"
    doctor_id: Optional[str]
    doctor_name: Optional[str]
    appointment_id: Optional[str]   # For reschedule/cancel
    requested_date: Optional[str]
    requested_time: Optional[str]
    missing_fields: List[str]       # What we're still waiting for
```

#### Usage:
```python
from app.services.conversation_state_machine import get_conversation_state_machine

state_machine = get_conversation_state_machine(redis_client)

# Get current state
state, context = await state_machine.get_state(session_id)
# Returns: (ConversationState.SERVICE_INQUIRY, StateContext(service_name="veneers"))

# Transition to new state
await state_machine.transition(
    session_id,
    ConversationState.BOOKING_NEW,
    {'service_name': 'veneers', 'doctor_id': 'uuid-123'}
)

# Check if context should be injected
should_inject, reason = await state_machine.should_inject_context(
    session_id,
    "checking Dr. Dan availability for filling"
)
# Returns: (False, "User focused on veneers, pending action unrelated")
```

### 3. 📝 Focused Assistant Prompts

**File**: `app/prompts/focused_assistant_prompt.py` (NEW)

Clear system prompts that instruct the LLM to handle ONE task at a time.

#### Key Instructions:

```python
CRITICAL RULES:

1. ONE TASK AT A TIME: Focus on the user's MOST RECENT request.
   Do not mix multiple appointments or requests.

2. NEW REQUEST DETECTION: If the user asks about something NEW:
   - Acknowledge the pending task briefly
   - Ask which one they want to handle first
   - Wait for their choice before proceeding

3. USER CONTROLS THE FLOW: If the user says "I don't need [X]",
   immediately FORGET about [X] and focus on what they want.

WHAT TO AVOID:
❌ "Let's confirm your appointment with Dr. Dan for a filling. Also, here's veneer info..."
✅ "I see you're asking about veneers. Should we proceed with that, or would you like to finalize the Dr. Dan appointment first?"
```

#### State-Aware Prompts:

```python
# Get prompt that's aware of current conversation state
prompt = get_state_aware_prompt(
    current_state='service_inquiry',
    state_summary='Currently helping user learn about: veneers'
)
# Returns: Base prompt + "Focus on answering questions about THIS service"
```

---

## Integration Guide

### Step 1: Update Context Injection Logic

**File**: `app/api/multilingual_message_processor.py`

**Before** (lines 333-364):
```python
# Check if agent has pending action
if session_turn_status == 'agent_action_pending' and last_agent_action:
    # ALWAYS inject context (BAD!)
    additional_context = f"""
⚠️ CRITICAL CONTEXT - YOU PREVIOUSLY PROMISED TO FOLLOW UP:
In your last message, you told the user: "{last_agent_action}"
...
"""
    logger.warning(f"⚠️ Injecting pending action context: {last_agent_action}")
```

**After**:
```python
# Check if agent has pending action
if session_turn_status == 'agent_action_pending' and last_agent_action:
    # NEW: Check relevance before injecting
    from app.services.context_relevance_checker import get_context_relevance_checker
    from app.services.conversation_state_machine import get_conversation_state_machine

    checker = get_context_relevance_checker()
    state_machine = get_conversation_state_machine(self.redis)

    # Check if pending action is relevant to current message
    is_relevant, confidence, reasoning = await checker.is_context_relevant(
        current_message=sanitized_message,
        pending_action=last_agent_action,
        conversation_history=session_messages[-5:]
    )

    # Also check conversation state
    should_inject, state_reason = await state_machine.should_inject_context(
        session_id, last_agent_action
    )

    # Only inject if BOTH checks pass
    if is_relevant and confidence > 0.6 and should_inject:
        additional_context = f"""
⚠️ PENDING CONTEXT (RELEVANT TO CURRENT REQUEST):
In your last message, you told the user: "{last_agent_action}"

The user is now following up on this. Provide the information you promised.
"""
        logger.info(f"✅ Injecting relevant context (confidence: {confidence:.2f}): {last_agent_action}")
    else:
        # Context not relevant - user is asking about something else
        logger.info(
            f"❌ Skipping context injection - Not relevant\n"
            f"  Relevance: {is_relevant} (confidence: {confidence:.2f}) - {reasoning}\n"
            f"  State allows: {should_inject} - {state_reason}"
        )
        additional_context = ""
```

### Step 2: Use Focused Prompts

**File**: `app/api/multilingual_message_processor.py`

**Before**:
```python
system_prompt = "You are a healthcare receptionist..."
```

**After**:
```python
from app.prompts.focused_assistant_prompt import get_state_aware_prompt, get_context_injection_warning

# Get current conversation state
from app.services.conversation_state_machine import get_conversation_state_machine
state_machine = get_conversation_state_machine(self.redis)
current_state, state_context = await state_machine.get_state(session_id)
state_summary = await state_machine.get_state_summary(session_id)

# Use state-aware system prompt
system_prompt = get_state_aware_prompt(
    current_state=current_state.value,
    state_summary=state_summary
)

# Add context injection warning if context is being injected
if additional_context:
    system_prompt += "\n\n" + get_context_injection_warning()
```

### Step 3: Update State on User Messages

**File**: `app/api/multilingual_message_processor.py`

Add state tracking based on user intent:

```python
# After extracting intent from user message
intent_info = await checker.extract_current_intent(
    message=sanitized_message,
    language=detected_language
)

# Update conversation state based on intent
if intent_info.get('is_new_request'):
    # User starting a new request - reset or transition state
    if intent_info['intent'] == 'service_inquiry':
        await state_machine.transition(
            session_id,
            ConversationState.SERVICE_INQUIRY,
            {
                'service_name': intent_info['entities'].get('service_name'),
                'doctor_name': intent_info['entities'].get('doctor_name')
            }
        )
    elif intent_info['intent'] == 'book_appointment':
        await state_machine.transition(
            session_id,
            ConversationState.BOOKING_NEW,
            {
                'service_name': intent_info['entities'].get('service_name'),
                'requested_date': intent_info['entities'].get('date')
            }
        )
```

---

## Testing

### Test Case 1: New Request While Pending Action Exists

**Scenario**:
1. Agent promises to check Dr. Dan availability for filling (pending action set)
2. User asks: "Хочу узнать про виниры" ("Want to know about veneers")

**Expected Behavior**:
- Context relevance checker returns: `(False, 0.0, "Different service")`
- State machine: Context not injected
- Agent responds ONLY about veneers
- No mention of Dr. Dan filling unless user asks

**Test Code**:
```python
# Test relevance checker
is_relevant, confidence, reasoning = await checker.is_context_relevant(
    current_message="Хочу узнать про виниры",
    pending_action="checking Dr. Dan availability for filling"
)

assert is_relevant == False
assert confidence < 0.3
assert "different" in reasoning.lower() or "unrelated" in reasoning.lower()
```

### Test Case 2: Actual Follow-up on Pending Action

**Scenario**:
1. Agent promises to check Dr. Dan availability for filling
2. User asks: "Нашли время для записи?" ("Did you find a time for the appointment?")

**Expected Behavior**:
- Context relevance checker returns: `(True, 0.9, "User following up on promised action")`
- State machine: Allows context injection
- Agent responds with follow-up information about Dr. Dan filling

**Test Code**:
```python
is_relevant, confidence, reasoning = await checker.is_context_relevant(
    current_message="Нашли время для записи?",
    pending_action="checking Dr. Dan availability for filling"
)

assert is_relevant == True
assert confidence > 0.7
assert "follow" in reasoning.lower() or "related" in reasoning.lower()
```

### Test Case 3: Explicit Negation

**Scenario**:
1. Agent brings up Dr. Dan appointment
2. User says: "Мне не нужен дан с пломбой" ("I don't need Dan for a filling")

**Expected Behavior**:
- Fast heuristic catches "не нужен" (Russian negation)
- Returns: `(False, 0.0, "User explicitly rejected pending action")`
- Agent immediately stops mentioning Dr. Dan
- Focuses on what user DOES want

**Test Code**:
```python
is_relevant, confidence, reasoning = await checker.is_context_relevant(
    current_message="Мне не нужен дан с пломбой",
    pending_action="confirming Dr. Dan appointment for filling"
)

assert is_relevant == False
assert "reject" in reasoning.lower() or "negation" in reasoning.lower()
```

---

## Benefits

### Before Fix:
```
User: Хочу узнать про виниры
      (Want to know about veneers)

Agent: Хорошо! Кстати, у вас есть запись к Дану на пломбу.
       Подтверждаете? Также про виниры...
       (OK! By the way, you have an appointment with Dan for a filling.
       Confirming? Also about veneers...)

User: Мне не нужен дан с пломбой
      (I don't need Dan for a filling)

Agent: Понял. Но давайте подтвердим время с Даном...
       (Understood. But let's confirm time with Dan...)

[FRUSTRATION]
```

### After Fix:
```
User: Хочу узнать про виниры
      (Want to know about veneers)

[System checks: Not related to pending Dr. Dan appointment]
[System sets state: SERVICE_INQUIRY(service_name="veneers")]

Agent: Конечно! Виниры - это...
       (Of course! Veneers are...)

[NO MENTION OF DR. DAN]

User: Сколько стоит?
      (How much does it cost?)

Agent: Стоимость виниров...
       (The cost of veneers...)

[STAYS FOCUSED ON CURRENT REQUEST]
```

---

## Monitoring

### Metrics to Track:

1. **Context Injection Rate**:
   - Before: 100% (always injected)
   - Target: <30% (only when relevant)

2. **User Confusion Signals**:
   - "I don't need [X]" frequency
   - "I'm asking about [Y]" clarifications
   - Target: 90% reduction

3. **Task Completion Rate**:
   - Single-task conversations completed
   - Target: >85%

4. **Context Relevance Accuracy**:
   - Manual review of 100 conversations
   - Target: >90% correct relevance decisions

### Logging:

```python
# Log context relevance decisions
logger.info(
    f"Context relevance: {is_relevant} "
    f"(confidence: {confidence:.2f}) "
    f"Reasoning: {reasoning}"
)

# Log state transitions
logger.info(f"State transition: {old_state} → {new_state}")

# Log context injection decisions
if additional_context:
    logger.info("✅ Injecting relevant context")
else:
    logger.info("❌ Skipping irrelevant context")
```

---

## Rollout Plan

### Phase 1: Testing (Week 1)
- [ ] Deploy to staging environment
- [ ] Run automated tests
- [ ] Manual testing with sample conversations
- [ ] Review logs for edge cases

### Phase 2: Gradual Rollout (Week 2)
- [ ] Deploy to 10% of production traffic
- [ ] Monitor metrics closely
- [ ] Gather user feedback
- [ ] Adjust confidence thresholds if needed

### Phase 3: Full Deployment (Week 3)
- [ ] Roll out to 100% of traffic
- [ ] Continue monitoring
- [ ] Document edge cases
- [ ] Train support team on new behavior

---

## Edge Cases

### Case 1: Ambiguous Follow-up

**User**: "А как насчет времени?" ("What about the time?")

**Challenge**: Could refer to pending appointment OR new request

**Solution**: State machine checks current state
- If state is BOOKING_NEW: Assume referring to new appointment
- If state is AWAITING_INFO: Assume providing requested time
- If state is IDLE: Ask for clarification

### Case 2: Multiple Pending Actions

**Scenario**: User has two pending actions (rare but possible)

**Solution**:
- Prioritize most recent pending action
- If user's message matches neither, treat as new request
- Ask user to clarify which task they want to continue

### Case 3: Context Relevance False Negative

**Issue**: Checker says not relevant when it actually is

**Mitigation**:
- Conservative confidence threshold (0.6)
- Log all decisions for review
- A/B test different thresholds
- Manual review of low-confidence rejections

---

## Files Created/Modified

**New Files**:
1. ✅ `app/services/context_relevance_checker.py` - Relevance checking service
2. ✅ `app/services/conversation_state_machine.py` - State management
3. ✅ `app/prompts/focused_assistant_prompt.py` - Improved prompts
4. ✅ `CONTEXT_CONFUSION_FIX.md` - This documentation

**Files to Modify**:
5. ⏳ `app/api/multilingual_message_processor.py` - Integrate new services
6. ⏳ `app/services/router_service.py` - Update state transitions

---

## Conclusion

This fix addresses the root cause of context confusion by:

1. ✅ **Checking relevance** before injecting context
2. ✅ **Managing state** explicitly to prevent bleeding
3. ✅ **Instructing LLM** to focus on one task at a time
4. ✅ **Detecting user intent** to identify new vs. follow-up requests
5. ✅ **Respecting user choice** when they say "I don't need [X]"

**Expected Impact**:
- 90% reduction in context confusion incidents
- Higher user satisfaction
- Clearer conversation flow
- More accurate appointment bookings

🎉 **Users will no longer be frustrated by agents mixing up different appointments!**