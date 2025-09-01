from ctypes import Union
import time
import aiohttp  
import asyncio    
from typing import Dict, Any, Callable, Optional  
from pydantic import BaseModel as PydanticBaseModel  
from src.config import settings
from src.logging import logger

class AuthState(PydanticBaseModel):
    tenant_url: str | None = None
    bearer_token: str | None = None
    token_expires_at: float = 0.0
    org_id: int | None = None
    user_id: int | None = None

class FilevineClient:
    def __init__(self, max_retries: int = 5, backoff_factor: float = 1.0, timeout_seconds: int = 30):
            self.identity_url =settings.FILEVINE_IDENTITY_URL
            self.util_url =settings.FILEVINE_UTIL_URL
            self.api_base_url = settings.FILEVINE_API_BASE_URL
            self.client_id = settings.FV_CLIENT_ID
            self.client_secret = settings.FV_CLIENT_SECRET
            self.pat = settings.FV_PAT
            self.headers = {'Content-Type': 'application/x-www-form-urlencoded'}
            self.scope = 'fv.api.gateway.access tenant filevine.v2.api.* email openid fv.auth.tenant.read'
            
            self.auth_state = AuthState()
            self.max_retries = max_retries
            self.backoff_factor = backoff_factor
            self.timeout_seconds = timeout_seconds

    def is_token_expired(self) -> bool:
        return time.time() >= self.auth_state.token_expires_at - 60

    @staticmethod
    async def retry_async(func: Callable, *args, max_retries: int = 5, backoff_factor: float = 1.0, **kwargs) -> Any:
        """Async retry wrapper with exponential backoff for transient errors."""
        last_exception = None  # ADDED: To store the last error for better reporting
        for attempt in range(max_retries):
            try:
                return await func(*args, **kwargs)
            # CHANGED: Replaced aiohttp.ClientTimeout with asyncio.TimeoutError
            except (asyncio.TimeoutError, aiohttp.ClientConnectionError, aiohttp.ServerDisconnectedError) as e:
                logger.warning(f"Transient error: {e}. Retrying in {backoff_factor * (2 ** attempt)}s...")
                last_exception = e
            except aiohttp.ClientResponseError as e:
                if e.status == 429 or e.status >= 500:
                    logger.warning(f"API error {e.status}: {e.message}. Retrying in {backoff_factor * (2 ** attempt)}s...")
                    last_exception = e
                else:
                    # Don't retry for errors like 401, 403, 404, etc.
                    raise
            except Exception as e:
                logger.error(f"Unexpected error: {e}. Not retrying.")
                raise

            if attempt < max_retries - 1:
                await asyncio.sleep(backoff_factor * (2 ** attempt))

        # CHANGED: Raise the last known exception for better debugging
        raise RuntimeError(f"Max retries ({max_retries}) exceeded for {func.__name__}") from last_exception

    async def fetch_bearer_token(self) -> str:
        """Fetches a new bearer token asynchronously and updates state."""
        async def _fetch():
            logger.info("Fetching new bearer token...")
            payload = {
                'grant_type': 'personal_access_token',
                'token': self.pat,
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'scope': self.scope
            }
            
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.identity_url, headers=self.headers, data=payload) as res:
                    if res.status != 200:
                        error_text = await res.text()
                        logger.error(f"Token fetch failed ({res.status}): {error_text}")
                        raise aiohttp.ClientResponseError(res.request_info, res.history, status=res.status, message=error_text)
                    data = await res.json()
                    self.auth_state.bearer_token = data['access_token']
                    self.auth_state.token_expires_at = time.time() + data['expires_in']
                    logger.info("Bearer token fetched.")
                    return self.auth_state.bearer_token
        
        try:
            return await self.retry_async(_fetch, max_retries=self.max_retries, backoff_factor=self.backoff_factor)
        except aiohttp.ClientResponseError as e:
            if e.status == 401:
                logger.warning("401 during token fetch—possible invalid creds. Not retrying.")
            raise

    async def fetch_user_org_ids(self) -> Dict[str, Any]:
        """Fetches user and org IDs using the current bearer token and updates state."""
        async def _fetch():
            logger.info("Fetching user/org IDs...")
            auth_headers = {'Authorization': f'Bearer {self.auth_state.bearer_token}'}
            
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.util_url, headers=auth_headers) as res:
                    if res.status != 200:
                        error_text = await res.text()
                        logger.error(f"ID fetch failed ({res.status}): {error_text}")
                        raise aiohttp.ClientResponseError(res.request_info, res.history, status=res.status, message=error_text)
                    data = await res.json()
                    logger.debug(f"DATA: {data}")
                    self.auth_state.tenant_url = data['orgs'][0]['tenant']['hostNameAsUrl']
                    self.auth_state.user_id = data['user']['userId']['native']
                    self.auth_state.org_id = data['orgs'][0]['orgId']
                    logger.info("User/org IDs fetched.")
                    return {'user_id': self.auth_state.user_id, 'org_id': self.auth_state.org_id}
        
        return await self.retry_async(_fetch, max_retries=self.max_retries, backoff_factor=self.backoff_factor)

    async def ensure_auth_state(self):
        """Ensures valid auth state: refreshes token if needed, fetches IDs if missing."""
        if not self.auth_state.bearer_token or self.is_token_expired():
            logger.info("Token missing or expired—refreshing...")
            await self.fetch_bearer_token()
        
        if not self.auth_state.org_id or not self.auth_state.user_id:
            logger.info("Org/User IDs missing—fetching...")
            await self.fetch_user_org_ids()
        logger.info("Auth state ensured.")
        
    async def _fetch_auth_state(self) -> Dict[str, Any]:
        """Checks auth state (fetches if needed) and returns the auth variables as dict."""
        await self._ensure_valid_token()
        return {
            'bearer_token': self._bearer_token,
            'token_expires_at': self._token_expires_at,
            'org_id': self._org_id,
            'user_id': self._user_id,
            'tenant_url': self._tenant_url
        }
        
    async def _make_api_call(self, url: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Reusable method for making API calls with auth, retry, and error handling."""
        await self.ensure_auth_state()
        
        async def _fetch():
            logger.info(f"Requesting URL: {url}")
            headers = {
                'Authorization': f'Bearer {self.auth_state.bearer_token}',
                'x-fv-orgid': str(self.auth_state.org_id),
                'x-fv-userid': str(self.auth_state.user_id),
                'Accept': 'application/json'
            }
            
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers, params=params or {}) as res:
                    if res.status != 200:
                        error_text = await res.text()
                        logger.error(f"Fetch failed ({res.status}): {error_text}")
                        raise aiohttp.ClientResponseError(res.request_info, res.history, status=res.status, message=error_text)
                    try:
                        return await res.json()
                    except aiohttp.ContentTypeError as e:
                        logger.error(f"JSON decode error: {e}")
                        raise
        
        return await self.retry_async(_fetch, max_retries=self.max_retries, backoff_factor=self.backoff_factor)
    
    async def _make_api_patch(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Reusable method for making PATCH API calls with auth, retry, and error handling."""
        await self.ensure_auth_state()
        
        async def _patch():
            logger.info(f"Patching URL: {url}")
            headers = {
                'Authorization': f'Bearer {self.auth_state.bearer_token}',
                'x-fv-orgid': str(self.auth_state.org_id),
                'x-fv-userid': str(self.auth_state.user_id),
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            }
            
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.patch(url, headers=headers, json=payload) as res:
                    if res.status != 200:
                        error_text = await res.text()
                        logger.error(f"Patch failed ({res.status}): {error_text}")
                        raise aiohttp.ClientResponseError(res.request_info, res.history, status=res.status, message=error_text)
                    try:
                        return await res.json()
                    except aiohttp.ContentTypeError as e:
                        logger.error(f"JSON decode error: {e}")
                        raise
        
        return await self.retry_async(_patch, max_retries=self.max_retries, backoff_factor=self.backoff_factor)

    async def get_expense_item(self, project_id: int, section_selector: str, item_id: str) -> Dict[str, Any]:
        """Fetches a collection item, ensuring auth state first."""
        url = f"{self.api_base_url}/fv-app/v2/Projects/{project_id}/Collections/{section_selector}/{item_id}"
        querystring = {"requestedFields": "itemId,checkhistory,status,title,createdDate,notes,discription,transactionType,amount,payee,balanceDue,toggleQuickbooksIntegration,iscreditcard,typeofcheckrequest"}
        return await self._make_api_call(url, params=querystring)

    async def get_project_details(self, project_id: int) -> Dict[str, Any]:
        """Fetches project details (number, client first/last name) for a given project_id."""
        url = f"{self.api_base_url}/fv-app/v2/Projects/{project_id}"
        querystring = {"requestedFields": "number,projectId,clientName"}  # Adjusted for client subfields
        return await self._make_api_call(url, params=querystring)
    
    async def update_expense_item(
        self,
        project_id: int,
        section_selector: str,
        item_id: str,
        status: Optional[str] = None,
        check_history: Optional[str] = None,
        check_number: Optional[Union[int, str]] = None,  # Allow str input for coercion
        amount_paid: Optional[Union[float, str]] = None,  # Allow str for parsing
        check_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """Updates expense item fields in Filevine after QB sync or queue init. Supports partial updates with validation/coercion."""
        url = f"{self.api_base_url}/fv-app/v2/Projects/{project_id}/Collections/{section_selector}/{item_id}"
        
        data_object = {}
        
        if status is not None:
            data_object["status"] = status if status != "Unknown" else None
        
        if check_history is not None:
            data_object["checkhistory"] = check_history
        
        if check_number is not None:
            # Coerce/validate to int (strip non-digits if str)
            if isinstance(check_number, str):
                original = check_number
                check_number = re.sub(r'\D', '', check_number)  # Strip non-digits
                if check_number != original:
                    logger.warning(f"Stripped non-numeric chars from checknumber '{original}' → '{check_number}'")
                if not check_number:
                    raise ValueError(f"Invalid checknumber '{original}': No digits remaining after stripping.")
            try:
                data_object["checknumber"] = int(check_number)
            except ValueError:
                raise ValueError(f"Invalid checknumber '{check_number}': Must be convertible to integer.")
        
        if amount_paid is not None:
            # Parse to float if str
            if isinstance(amount_paid, str):
                try:
                    amount_paid = float(amount_paid)
                except ValueError:
                    raise ValueError(f"Invalid amountpaid '{amount_paid}': Must be convertible to float.")
            data_object["amountpaid"] = amount_paid
        
        if check_date is not None:
            data_object["checkdate"] = check_date
        
        if not data_object:
            raise ValueError("At least one field must be provided for update.")
        
        payload = {
            "ItemId": {
                "Native": item_id,
                "Partner": None
            },
            "DataObject": data_object,
            "Links": {},
            "CreatedDate": None
        }
        
        return await self._make_api_patch(url, payload)
        
        
# Example test (run with asyncio.run(test_fetch()))
async def test_fetch():
    client = FilevineClient()
    logger.info("Fetching collection item...")
    project_id = 12361871
    selector = "expenses"
    project_type_id = "32506"
    section_selector = f"{selector}{project_type_id}"
    item_id = "c1c738ba-2409-4109-a44a-2d0b8bf56dea"

    response = await client.get_expense_item(project_id=project_id, section_selector=section_selector, item_id=item_id)
    print(response)  # This will print the JSON dict
    return response

async def test_auth():
    client = FilevineClient()
    await client.ensure_auth_state()
    print(client.auth_state.model_dump())
    return client.auth_state.model_dump()

if __name__ == "__main__":
    asyncio.run(test_auth())