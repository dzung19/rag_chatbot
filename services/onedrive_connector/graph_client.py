"""Microsoft Graph API client for OneDrive/SharePoint.

Uses O365 library for authentication and file operations.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Generator

from O365 import Account, FileSystemTokenBackend

logger = logging.getLogger(__name__)


class GraphClient:
    """Microsoft Graph API client for OneDrive/SharePoint file operations via O365."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
    ):
        credentials = (client_id, client_secret)
        token_dir = os.path.dirname(__file__)
        token_backend = FileSystemTokenBackend(token_path=token_dir, token_filename="o365_token.txt")
        
        self.account = Account(
            credentials, 
            auth_flow='authorization', 
            tenant_id=tenant_id, 
            token_backend=token_backend
        )

        if self.account.is_authenticated:
            self.sharepoint = self.account.sharepoint()
        else:
            self.sharepoint = None

    @property
    def is_authenticated(self) -> bool:
        return self.account.is_authenticated

    def get_auth_url(self, redirect_uri: str) -> str:
        """Generate the Microsoft login URL."""
        scopes = ['Sites.Read.All', 'Files.Read.All']
        url, state = self.account.con.get_authorization_url(
            requested_scopes=scopes,
            redirect_uri=redirect_uri
        )
        return url

    def process_auth_callback(self, current_url: str, redirect_uri: str) -> bool:
        """Exchange the code in the callback URL for an access token."""
        return self.account.con.request_token(
            current_url,
            redirect_uri=redirect_uri
        )

    def get_site(self, domain: str, site_path: str):
        """Get the O365 Site object for a given SharePoint domain and path."""
        # Ensure site_path starts with a slash
        if not site_path.startswith("/"):
            site_path = f"/{site_path}"
            
        site_id = f"{domain}:{site_path}"
        site = self.sharepoint.get_site(site_id)
        if not site:
            raise ValueError(f"Site not found: {site_id}")
            
        logger.info("Resolved site %s", site_id)
        return site

    def get_drive(self, site, drive_name: str):
        """Get the O365 Drive object for a specific document library (drive) by name."""
        drives = site.get_drives()
        for drive in drives:
            if drive.name == drive_name:
                logger.info("Resolved drive '%s'", drive_name)
                return drive
                
        raise ValueError(f"Drive '{drive_name}' not found in site")

    def walk_drive_files(self, folder) -> Generator[tuple[str, bytes], None, None]:
        """Recursively yield all (filename, content) in a drive or folder."""
        items = folder.get_items()
        for item in items:
            if item.is_file:
                logger.info("Downloading %s...", item.name)
                content = self.get_file_content(item)
                yield item.name, content
            elif item.is_folder:
                yield from self.walk_drive_files(item)

    def get_file_content(self, item) -> bytes:
        """Download file content using O365 into a temporary buffer and return bytes."""
        with tempfile.TemporaryDirectory() as temp_dir:
            success = item.download(to_path=temp_dir, name=item.name)
            if success:
                file_path = os.path.join(temp_dir, item.name)
                with open(file_path, "rb") as f:
                    return f.read()
            else:
                raise Exception(f"Failed to download {item.name} from O365.")

    def close(self):
        """Cleanup resources if needed."""
        pass
