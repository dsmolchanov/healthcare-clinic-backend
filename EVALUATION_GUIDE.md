# Dental Agent Quality Evaluation Guide

This guide explains how to run quality evaluations for the Dental Clinic Agent. Unlike unit tests which check for code correctness, these evaluations check for **intelligence, politeness, and adherence to instructions** using a real LLM Judge.

## Prerequisites

1.  **OpenAI API Key**: You must have `OPENAI_API_KEY` set in your environment or `.env` file. This is used by both the Agent (to generate responses) and the Judge (to grade them).
2.  **Dependencies**: Ensure you have installed the requirements:
    ```bash
    pip install -r apps/healthcare-backend/requirements.txt
    ```

## Running Evaluations

To run the evaluation suite:

```bash
# From the project root
export PYTHONPATH=$PYTHONPATH:$(pwd)/apps/healthcare-backend
python3 apps/healthcare-backend/evaluation/run_evals.py
```

### Expected Output

The script will run through each scenario defined in `scenarios.yaml` and print a report:

```text
Running Scenario: Happy Path Booking
Input: Hi, I'd like to book a cleaning for tomorrow morning.
Agent Response: Hello! I can certainly help with that. We have openings at 9 AM and 11 AM tomorrow. Which works best for you?
Score: 10/10
Pass: ✅
Reasoning: The agent acknowledged the request, offered specific times, and maintained a polite tone.

...

--- Evaluation Summary ---
Total Scenarios: 4
Passed: 4
Failed: 0
Success Rate: 100.0%
```

## Adding New Scenarios

To add a new test case, edit `apps/healthcare-backend/evaluation/scenarios.yaml`.

**Format:**

```yaml
- name: "Scenario Name"
  description: "Brief description of what is being tested."
  messages:
    - role: "user"
      content: "The user's input message."
  expected_behavior: "What the agent SHOULD do. Be descriptive."
  criteria:
    - "Specific point 1 to check"
    - "Specific point 2 to check"
```

### Example: Testing Language Switching

```yaml
- name: "Spanish Switch"
  description: "User switches to Spanish mid-conversation."
  messages:
    - role: "user"
      content: "Hola, ¿tienen citas para hoy?"
  expected_behavior: "The agent should detect Spanish and respond in Spanish."
  criteria:
    - "Response is in Spanish"
    - "Answers the availability question"
```

## Troubleshooting

-   **Authentication Error**: Ensure `OPENAI_API_KEY` is set.
-   **Import Errors**: Make sure `PYTHONPATH` includes `apps/healthcare-backend`.
-   **Failures**: If a scenario fails, check the "Reasoning" provided by the Judge. It usually explains exactly why the response was considered poor (e.g., "Agent failed to provide price information").
