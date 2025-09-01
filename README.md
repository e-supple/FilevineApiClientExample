# Asynchronous Filevine API Client for Python

This repository provides a robust, asynchronous Python client for interacting with the Filevine API. It was created to simplify the authentication process and provide a resilient foundation for making API calls.

The client handles the multi-step authentication flow automatically, manages token expiration and renewal, and implements an exponential backoff retry mechanism for handling transient network errors and API rate limiting.

## Motivation

The Filevine API authentication process involves several steps:
1.  Exchanging a Personal Access Token (PAT) for a short-lived Bearer Token.
2.  Using the Bearer Token to fetch the current User ID and Organization ID.
3.  Using the Bearer Token, User ID, and Org ID in headers for all subsequent API requests.
4.  Refreshing the Bearer Token when it expires.

This client encapsulates all of this logic into a simple-to-use class, so you can focus on interacting with the API endpoints you need.

## Features

-   **Asynchronous:** Built with `aiohttp` for high-performance, non-blocking API calls.
-   **Automatic Authentication:** Handles the complete PAT -> Bearer Token -> User/Org ID flow.
-   **Automatic Token Refresh:** Detects expired tokens and seamlessly fetches a new one before making a request.
-   **Resilient:** Implements an async-friendly retry mechanism with exponential backoff for transient errors (e.g., connection issues, timeouts, `5xx` server errors, `429` rate limiting).
-   **Configuration Driven:** Easily configure your credentials using environment variables.
-   **Extensible:** Provides reusable internal methods (`_make_api_call`, `_make_api_patch`) to easily add support for more Filevine API endpoints.

## Prerequisites

-   Python 3.8+
-   Filevine API Credentials:
    -   **Client ID** (`FV_CLIENT_ID`)
    -   **Client Secret** (`FV_CLIENT_SECRET`)
    -   **Personal Access Token (PAT)** (`FV_PAT`)

You can generate these credentials from your Filevine Developer Portal.

## Installation

1.  **Clone the repository:**
    ```bash
    git clone <your-repo-url>
    cd <your-repo-directory>
    ```

2.  **Create a virtual environment (recommended):**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
    ```

3.  **Install the required packages:**

    It's recommended to create a `requirements.txt` file.

    **`requirements.txt`**
    ```
    aiohttp
    pydantic
    python-dotenv
    ```

    Then, install them using pip:
    ```bash
    pip install -r requirements.txt
    ```

## Configuration

The client is configured using environment variables.

1.  Create a file named `.env` in the root of your project directory.
2.  Copy the contents of `.env.example` into it and fill in your Filevine API credentials.

**`.env.example`**
```env
# Filevine API Credentials
FV_CLIENT_ID="YOUR_CLIENT_ID_HERE"
FV_CLIENT_SECRET="YOUR_CLIENT_SECRET_HERE"
FV_PAT="YOUR_PERSONAL_ACCESS_TOKEN_HERE"

# --- FIX 1: Corrected the URLs to be plain text ---
# Filevine API URLs (Defaults are usually correct)
FILEVINE_IDENTITY_URL="[https://identity.filevine.com/connect/token](https://identity.filevine.com/connect/token)"
FILEVINE_UTIL_URL="[https://util.filevine.com/user](https://util.filevine.com/user)"
FILEVINE_API_BASE_URL="[https://api.filevine.io](https://api.filevine.io)"
```
*Note: The `src/config.py` module should be set up to load these variables (e.g., using `python-dotenv` and `pydantic-settings`).*

## Usage

Instantiate the `FilevineClient` and call its methods within an `async` function. The client will handle authentication automatically.

Here is a complete example of how to fetch a specific expense item from a project.

```python
import asyncio
from src.filevine_client import FilevineClient # Adjust import path as needed
from src.logging import logger # Your configured logger

async def main():
    """
    An example function to demonstrate fetching data using the FilevineClient.
    """
    try:
        # 1. Create an instance of the client
        client = FilevineClient()

        # 2. Define the parameters for the API call
        project_id = 12345678
        project_type_id = "32506"
        section_selector = f"expenses{project_type_id}"
        item_id = "c1c738ba-2409-4109-a44a-2d0b8bf56dea"

        logger.info(f"Fetching expense item '{item_id}' from project '{project_id}'...")

        # 3. Call the method
        # The client will automatically handle fetching/refreshing the token
        # and getting the Org/User IDs on the first call.
        expense_data = await client.get_expense_item(
            project_id=project_id,
            section_selector=section_selector,
            item_id=item_id
        )

        logger.info("Successfully fetched data:")
        print(expense_data)

        # Example of an update call
        logger.info("Updating the expense item's status...")
        update_response = await client.update_expense_item(
            project_id=project_id,
            section_selector=section_selector,
            item_id=item_id,
            status="Paid",
            check_number="12345"
        )

        logger.info("Successfully received update response:")
        print(update_response)

    except Exception as e:
        logger.error(f"An error occurred in the main execution: {e}", exc_info=True)

if __name__ == "__main__":
    # Run the async main function
    asyncio.run(main())
```

## How It Works: The Authentication Flow

When you call an API method like `get_expense_item` for the first time:
1.  The method calls `ensure_auth_state()`.
2.  `ensure_auth_state()` checks if a valid `bearer_token` exists and is not expired.
3.  Since it's the first run, it calls `fetch_bearer_token()`. This sends your PAT, Client ID, and Secret to the identity URL and stores the new bearer token and its expiration time.
4.  `ensure_auth_state()` then checks if `org_id` and `user_id` exist.
5.  Since they don't, it calls `fetch_user_org_ids()`. This uses the new bearer token to get the IDs from the util URL and stores them.
6.  Now that the auth state is fully populated, the original `get_expense_item` method proceeds with making the actual API call using the correct headers.

On subsequent calls, `ensure_auth_state()` will find a valid, non-expired token and will not need to re-authenticate, making the process very efficient.

## Extending the Client

You can easily add new methods to the `FilevineClient` class to support other API endpoints. Use the internal `_make_api_call` (for GET requests) and `_make_api_patch` (for PATCH requests) as a template.

### Example: Adding a method to get a project's notes

```python
# Inside the FilevineClient class

async def get_project_notes(self, project_id: int, page: int = 1, limit: int = 50) -> Dict[str, Any]:
    """
    Fetches a list of notes for a given project.
    """
    url = f"{self.api_base_url}/core/projects/{project_id}/notes"
    params = {
        "page": page,
        "limit": limit
    }
    # _make_api_call handles auth, headers, and retries for you!
    return await self._make_api_call(url, params=params)
```

## Advanced Usage: Dependency Injection in a Web Framework (e.g., FastAPI)

For long-running applications like a web server, you should only create **one instance** of the `FilevineClient` and share it across all requests. This is highly efficient as it prevents the client from re-authenticating on every API call and allows it to maintain its authentication state.

The best way to achieve this is with a singleton pattern, made easy by the dependency injection systems in modern frameworks like FastAPI. Here’s how to set it up:

#### Step 1: Create a Singleton Instance and a Dependency Provider

Create a central place for your client instance. This could be in a `dependencies.py` or `services.py` file.

**`src/dependencies.py`**
```python
from .filevine_client import FilevineClient

# 1. Create a single, shared instance of the client for the entire application.
#    This instance will be created once when the application starts.
filevine_client_instance = FilevineClient()

def get_filevine_client():
    """
    Dependency provider function. FastAPI will call this function
    for any route that depends on it. It simply returns the shared instance.
    """
    return filevine_client_instance
```

#### Step 2: "Depend" on the Client in Your API Route

Now, in your router file, you can simply "ask for" the `FilevineClient` in your route's parameters using `Depends`. FastAPI will handle running `get_filevine_client` and passing the instance to your route function as the `filevine` argument.

**`src/routers/items_router.py`**
```python
from fastapi import APIRouter, Depends, HTTPException
from src.dependencies import get_filevine_client # Adjust import path as needed
from src.filevine_client import FilevineClient    # Import for type hinting

router = APIRouter()

@router.get("/projects/{project_id}/items/{item_id}")
async def get_project_item_details(
    project_id: int,
    item_id: str,
    section_selector: str, # e.g., "expenses12345"
    # 2. Use Depends to inject the shared FilevineClient instance.
    filevine: FilevineClient = Depends(get_filevine_client)
):
    """
    An example API endpoint that fetches data from Filevine.
    The 'filevine' parameter is our ready-to-use, authenticated client.
    """
    try:
        # 3. Use the injected client to make API calls.
        #    The client will automatically manage its auth token.
        project_data = await filevine.get_project_details(project_id)

        expense_item = await filevine.get_expense_item(
            project_id=project_id,
            section_selector=section_selector,
            item_id=item_id
        )

        return {
            "projectName": project_data.get("clientName"),
            "projectNumber": project_data.get("number"),
            "expenseDetails": expense_item
        }
    
    except Exception as e:
        # In a real app, you would have more specific error handling.
        raise HTTPException(status_code=500, detail=f"An error occurred: {e}")
```

By using this pattern, your application remains clean, efficient, and easy to test, as you can easily swap out the dependency for a mock client during unit tests.

## Support This Project

If you find this client useful and it has saved you time and effort, please consider supporting its ongoing development. Your support is greatly appreciated!

### Primary Support (via GitHub Sponsors)

The easiest and most preferred way to support the project is by becoming a sponsor on GitHub. You can make a one-time or recurring donation.

[<img src="https://img.shields.io/static/v1?label=Sponsor&message=%E2%9D%A4&logo=GitHub&color=%23fe8e86" alt="Sponsor on GitHub" />](https://github.com/sponsors/e-supple)

### Alternative Support (via Cryptocurrency)

If you prefer to donate via cryptocurrency, you can use one of the addresses below:

-   **Bitcoin (BTC):** `bc1qfkx69qvv0e00dwxytmc0ngwhzlrc7k5986pf4g`
-   **Ethereum (ETH):** `0x237838c2d4192E01447961aB768Ba3C15F7144f5`
-   **Solana (SOL):** `46aN6QsjZut6zgiXUH4oA8oMZ8yuYD8zCXC5xNfYkRN9`

## Contributing

Feel free to open an issue or submit a pull request if you have suggestions for improvements or find any bugs.

## License

This project is licensed under the License. See the `LICENSE` file for details.