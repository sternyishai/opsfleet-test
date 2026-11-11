# AI Technical Assignment – Retail Company

## The Challenge: Design and Build a Data Analysis Chat Assistant

You are building a data analysis agent for a Retail Company's **non-technical executive teams**.

> **Client:** OpsFleet. **Project managers:** the Team Lead and Harry.

These executives need to ask complex questions about sales and performance, for example:

- *"Why are the users for state X underspending? And how does it compare to users in state Y?"*
- *"Why did our churn rate spike last month?"*
- *"Create a report for Q1 including insights and action items for Q2."*

### The agent will have access to

- **The Database:** A read-only connection to a SQL database containing raw transaction logs.
- **The "Golden Knowledge" Bucket:** A data lake containing historical "Trios" (**Question → SQL Query → Analyst Report**) created by human experts (theoretical).

### The Task

Design a production-ready, full **High-Level Design (HLD)**, with a detailed technical explanation, and build a **data analysis chat-agent prototype** that lets executives ask questions about this data in natural language and discuss the answers.

The system should be **easy to extend** with new capabilities (generating graphs, sending reports by email, searching the web for trends, etc.).

> **Note:** We expect to see which services you plan to use, how the components communicate, and how and where data is stored and handled. The "detailed technical explanation" must be detailed enough for us to understand how this system will function in production.
>
> Feel free to ask about any assumptions you need to make, or to follow up on requirements. We (the OpsFleet team) are the client for this project.

---

## Requirements

### 1. Hybrid Intelligence
The agent cannot rely on BigQuery data alone. It must use the **Golden Bucket** to understand how analysts previously interpreted questions and apply similar logic to new queries.
- Explain how you will **update the Golden Bucket over time**.
- Explain how you will **provide relevant data at query time**.

### 2. Safety & PII Masking
- The agent may **only answer analysis questions** and must be safeguarded against malicious users.
- The raw transaction logs contain sensitive data. The agent is **strictly forbidden from displaying PII** in the final output.
- Each user may **only analyze data on products related to them**.

### 3. High-Stakes Oversight (Destructive Ops)
While the DB is read-only, the agent manages a **"Saved Reports" library**. The agent must support inputs such as:
- *"Delete all reports mentioning Client X"*
- *"Delete all the reports we made in this conversation"*

These are destructive actions and require a **strict confirmation flow before execution, without breaking UX**. Users may delete **their own** reports.

### 4. Continuous Improvement (The Learning Loop)
1. **User level:** The agent should remember that "User A" prefers tables while "User B" prefers bullet points, and learn user preferences over time (e.g., how deep they like the analysis, whether they prefer charts or text).
2. **System level:** The agent should learn from past interactions and improve itself.

### 5. Resilience & Graceful Error Handling
- Detect **SQL syntax errors** or **empty results** and attempt to **self-correct** before giving up.
- Do this **without crashing the user interface** and **without inflating costs**.
- Be resilient to **API / third-party service failures and downtime**.

### 6. Quality Assurance
- How do you evaluate the agent **before deployment**?
- How do you verify that generated reports **answer the user's intent** correctly?
- How do you **evaluate UX**?

### 7. Observability
We need to know **when** the agent is failing and **why**.
- Define the **metrics** you would track at the agent level.
- Explain how you would support **deep-dive analysis / debugging** (understanding the message exchange and what went wrong).

### 8. Agility (Persona Management)
The CEO wants to change the **"tone"** of the reports weekly. The system must allow **non-developers** to update the agent's instructions **without redeployment**.

---

## Deliverables

### 1. Architecture Diagram (Mermaid, etc.)
Highlight the building blocks, services, compute, and flow of the system based on the requirements above. If you plan to use an infrastructure, framework, service, or data store for a building block, name it and explain why.

### 2. Detailed Technical Explanation
Cover the following:
1. Reasoning for the chosen cloud services, LLM models, and frameworks.
2. Data flow between components (where the HLD needs elaboration).
3. Error handling and fallback strategies.
4. Setup instructions and an example run.
5. A detailed explanation of how you handle or solve **each** of the requirements.

### 3. Working Code / Prototype
Build a chat agent that lets users ask data questions in natural language, discuss the answers, and **create a report with action items** when asked.

The prototype **must support** these requirements (the rest are design-only):

| # | Requirement | Prototype | Design |
|---|---|:---:|:---:|
| 1 | Hybrid Intelligence | | ✅ |
| 2 | Safety & PII Masking | ✅ | ✅ |
| 3 | High-Stakes Oversight | ✅ | ✅ |
| 4 | Continuous Improvement | | ✅ |
| 5 | Resilience & Graceful Error Handling | ✅ | ✅ |
| 6 | Quality Assurance | | ✅ |
| 7 | Observability | ✅ | ✅ |
| 8 | Agility (Persona Management) | | ✅ |

### 4. CLI-based interface for chat interactions

### 5. Runnable on another machine
Docker is not required, but provide proper setup instructions.

### 6. Framework choice
Use a framework of your choice. Explain **why** you chose it and your **level of experience** with it.

> **Assessment focus:** mainly the **system design**, the **technical explanation**, and an **elegant prototype** that fits the prototype requirements.

---

## Dataset Specification

- **Dataset:** `bigquery-public-data.thelook_ecommerce`
- **Required tables:**
  - `orders`: customer order information
  - `order_items`: individual items within orders
  - `products`: product catalog and details
  - `users`: customer demographics and information

## Expected Agent Capabilities

The prototype chat agent should be able to perform data analysis and generate insightful reports, such as:

- **Customer behavior:** e.g., top customers, total spend.
- **Product performance:** e.g., compare the performance of product X and Y, and explain why they differ.
- **Time-based metrics:** e.g., monthly revenue, up-to-date revenue by product.
- **Database structure:** answer questions about what data is available and what can be done with it.
- **Multi-step queries:** as described above.

### Technical notes
1. Use **BigQuery integration** to query and analyze the specified tables. The agent should construct and execute SQL queries dynamically based on the analysis requirements.
2. Preferably use one of the newer **Google Gemini** models. You can get a free API key from Google AI Studio. **Be mindful of rate limits.** If you use another provider, make sure Gemini is also supported, since that is what the client will use for testing.

---

## Setup Instructions (provided by the client)

### Environment setup
```bash
pip install -r requirements.txt
```

### BigQuery setup
1. Set up BigQuery access by following the [BigQuery Client Libraries documentation](https://cloud.google.com/bigquery/docs/reference/libraries) if you don't already have BigQuery access configured.
2. **Free tier:** Google Cloud provides 1 TB of free BigQuery compute per month, which is more than enough for this challenge.
3. **Authentication:** Make sure your environment is authenticated with Google Cloud so it can access the public datasets (e.g. `gcloud auth application-default login`).

---

## Time Expectation
This assignment is expected to take **6–12 hours** of work.

## Submission
Share a **public GitHub repository** containing:
- Documentation
- Source code
- Architecture diagram

---

## Resources

### Reference `BigQueryRunner` (provided by the client)

```python
import logging
from typing import Optional, List, Dict, Any
import pandas as pd
from google.cloud import bigquery


class BigQueryRunner:
    """A lean BigQuery client for executing SQL queries and returning DataFrame results."""

    def __init__(self, project_id: Optional[str] = None, dataset_id: Optional[str] = "bigquery-public-data.thelook_ecommerce") -> None:
        """Initialize BigQuery client.

        Args:
            project_id: Google Cloud project ID. If None, uses default credentials.
            dataset_id: BigQuery dataset ID. If None, uses default dataset.
        """
        logging.info("Initializing BigQuery client")
        try:
            self.client = bigquery.Client(project=project_id)
            self.dataset_id = dataset_id
            logging.info(f"BigQuery client initialized for dataset: {self.dataset_id}")
        except Exception as e:
            logging.error(f"Failed to initialize BigQuery client: {str(e)}")
            raise

    def execute_query(self, sql_query: str) -> pd.DataFrame:
        """Execute a SQL query and return results as a DataFrame.

        Args:
            sql_query: The SQL query to execute.

        Returns:
            DataFrame containing the query results.

        Raises:
            Exception: If query execution fails.
        """
        try:
            logging.info(f"Executing BigQuery query")
            query_job = self.client.query(sql_query)
            df = query_job.result().to_dataframe()
            logging.info(f"Query completed successfully, returned {len(df)} rows")
            return df
        except Exception as e:
            logging.error(f"BigQuery execution failed: {str(e)}")
            raise

    def get_table_schema(self, table_name: str) -> List[Dict[str, Any]]:
        """Get schema information for a specific table.

        Args:
            table_name: Name of the table (orders, order_items, products, users).

        Returns:
            List of dictionaries containing column information.
        """
        try:
            table_ref = f"{self.dataset_id}.{table_name}"
            table = self.client.get_table(table_ref)
            schema_info = []
            for field in table.schema:
                schema_info.append({
                    "name": field.name,
                    "type": field.field_type,
                    "mode": field.mode,
                    "description": field.description or ""
                })
            logging.info(f"Retrieved schema for table {table_name}")
            return schema_info
        except Exception as e:
            logging.error(f"Failed to get schema for table {table_name}: {str(e)}")
            raise
```
