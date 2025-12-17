# github.py
#
# GitHub PR Tracker Service for NVDA Web Services
# Tracks open pull requests for configured repositories
#

import json
import os
import time
import webbrowser
import urllib.request
import urllib.error
import urllib.parse

import addonHandler
import api
import config
import wx

import events
import service

addonHandler.initTranslation()

SERVICE_DISPLAY_NAME = _("GitHub PR Tracker")
API_BASE = "https://api.github.com"

# OAuth Device Flow configuration
# Note: You should register your own GitHub OAuth App and replace this client_id
# Go to: GitHub Settings -> Developer settings -> OAuth Apps -> New OAuth App
# Set the callback URL to anything (it's not used for device flow)
GITHUB_CLIENT_ID = "Ov23liYourClientIdHere"  # Replace with your actual client ID
OAUTH_DEVICE_CODE_URL = "https://github.com/login/device/code"
OAUTH_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
OAUTH_DEVICE_VERIFY_URL = "https://github.com/login/device"


class Service(service.Service):
    """GitHub PR Tracker Service - Track open PRs across repositories."""

    name = "GitHub"

    def __init__(self):
        super().__init__(self.name, SERVICE_DISPLAY_NAME)

        # Configuration
        self._token = None
        self._repositories = []
        self._configPath = None

        # State tracking
        self._prs = {}  # repo -> list of PRs
        self._prDetails = {}  # pr_url -> detailed PR data (reviews, comments)
        self._currentPR = None  # Currently selected PR for detail view
        self._inDetailView = False

        # Menu IDs
        self._settingsMenuId = None
        self._repoMenuIds = {}  # repo -> menuId

        # Refresh timing
        self._lastRefresh = 0
        self._refreshInterval = 60  # Refresh every 60 seconds

        # Dialog state (for wx thread safety)
        self._pendingDialogType = None
        self._pendingDialogResult = None

        # OAuth device flow state
        self._oauthDeviceCode = None
        self._oauthUserCode = None
        self._oauthPollInterval = 5
        self._oauthExpiresAt = 0
        self._oauthPolling = False
        self._lastOAuthPoll = 0

        # Load configuration
        self._loadConfig()

    def terminate(self):
        self._should_quit = True

    def _getConfigPath(self):
        """Get the path to the config file."""
        if self._configPath is None:
            configDir = os.path.join(config.getUserDefaultConfigPath(), "webServices", "github")
            os.makedirs(configDir, exist_ok=True)
            self._configPath = os.path.join(configDir, "config.json")
        return self._configPath

    def _loadConfig(self):
        """Load configuration from file."""
        try:
            configPath = self._getConfigPath()
            if os.path.exists(configPath):
                with open(configPath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._token = data.get("token")
                    self._repositories = data.get("repositories", [])
                    self.postLog(f"Loaded config: {len(self._repositories)} repositories")
        except Exception as ex:
            self.postLog(f"Failed to load config: {ex}")

    def _saveConfig(self):
        """Save configuration to file."""
        try:
            configPath = self._getConfigPath()
            with open(configPath, "w", encoding="utf-8") as f:
                json.dump({
                    "token": self._token,
                    "repositories": self._repositories
                }, f, indent=2)
            self.postLog("Configuration saved")
        except Exception as ex:
            self.postLog(f"Failed to save config: {ex}")

    # ========== GitHub API ==========

    def _apiRequest(self, endpoint, method="GET", data=None):
        """Make a GitHub API request."""
        if not self._token:
            return None

        url = f"{API_BASE}{endpoint}"
        headers = {
            "Authorization": f"token {self._token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "NVDA-WebServices-GitHub"
        }

        try:
            req = urllib.request.Request(url, headers=headers, method=method)
            if data:
                req.data = json.dumps(data).encode("utf-8")
                req.add_header("Content-Type", "application/json")

            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as ex:
            if ex.code == 401:
                self.postUserNotification(_("Invalid GitHub token"))
                self._token = None
                self._saveConfig()
            elif ex.code == 403:
                self.postUserNotification(_("GitHub API rate limit exceeded"))
            elif ex.code == 404:
                self.postLog(f"Not found: {endpoint}")
            else:
                self.postLog(f"API error {ex.code}: {ex.reason}")
            return None
        except Exception as ex:
            self.postLog(f"API request failed: {ex}")
            return None

    def _fetchPRs(self, repo):
        """Fetch open PRs for a repository."""
        prs = self._apiRequest(f"/repos/{repo}/pulls?state=open&sort=updated&direction=desc")
        if prs is None:
            return []

        # Fetch review status for each PR
        enrichedPRs = []
        for pr in prs:
            prNumber = pr["number"]

            # Get reviews
            reviews = self._apiRequest(f"/repos/{repo}/pulls/{prNumber}/reviews") or []

            # Determine approval status
            approvalStatus = self._getApprovalStatus(reviews)

            # Get CI status
            sha = pr["head"]["sha"]
            ciStatus = self._getCIStatus(repo, sha)

            enrichedPRs.append({
                "number": prNumber,
                "title": pr["title"],
                "author": pr["user"]["login"],
                "url": pr["html_url"],
                "head_sha": sha,
                "approval_status": approvalStatus,
                "ci_status": ciStatus,
                "reviews": reviews,
                "created_at": pr["created_at"],
                "updated_at": pr["updated_at"],
                "draft": pr.get("draft", False)
            })

        return enrichedPRs

    def _getApprovalStatus(self, reviews):
        """Determine the approval status from reviews."""
        # Group reviews by user, keeping only the latest
        latestReviews = {}
        for review in reviews:
            user = review["user"]["login"]
            state = review["state"]
            if state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                latestReviews[user] = state

        if not latestReviews:
            return "PENDING"

        # Check for changes requested
        if any(s == "CHANGES_REQUESTED" for s in latestReviews.values()):
            return "CHANGES_REQUESTED"

        # Check for approvals
        if any(s == "APPROVED" for s in latestReviews.values()):
            return "APPROVED"

        return "PENDING"

    def _getCIStatus(self, repo, sha):
        """Get CI status for a commit."""
        # Try check runs first (GitHub Actions)
        checkRuns = self._apiRequest(f"/repos/{repo}/commits/{sha}/check-runs")
        if checkRuns and checkRuns.get("check_runs"):
            runs = checkRuns["check_runs"]
            if any(r["conclusion"] == "failure" for r in runs):
                return "FAILURE"
            if any(r["status"] != "completed" for r in runs):
                return "PENDING"
            if all(r["conclusion"] == "success" for r in runs):
                return "SUCCESS"
            return "MIXED"

        # Try combined status (older status API)
        status = self._apiRequest(f"/repos/{repo}/commits/{sha}/status")
        if status:
            return status.get("state", "UNKNOWN").upper()

        return "NONE"

    # ========== OAuth Device Flow ==========

    def _startOAuthDeviceFlow(self):
        """Start the OAuth device flow authentication."""
        self.postLog("Starting OAuth device flow")
        self.postUserNotification(_("Starting GitHub authentication..."))

        # Request device code
        data = urllib.parse.urlencode({
            "client_id": GITHUB_CLIENT_ID,
            "scope": "repo read:org"
        }).encode("utf-8")

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        try:
            req = urllib.request.Request(OAUTH_DEVICE_CODE_URL, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))

            self._oauthDeviceCode = result.get("device_code")
            self._oauthUserCode = result.get("user_code")
            self._oauthPollInterval = result.get("interval", 5)
            expiresIn = result.get("expires_in", 900)
            self._oauthExpiresAt = time.time() + expiresIn
            verificationUri = result.get("verification_uri", OAUTH_DEVICE_VERIFY_URL)

            if not self._oauthDeviceCode or not self._oauthUserCode:
                self.postUserNotification(_("Failed to get device code from GitHub"))
                return

            # Copy the user code to clipboard using NVDA's API
            api.copyToClip(self._oauthUserCode)

            # Notify user and open browser
            self.postUserNotification(
                _("Your code {code} has been copied to the clipboard. Opening browser to authorize...").format(
                    code=self._oauthUserCode
                )
            )

            # Open the verification URL
            webbrowser.open(verificationUri)

            # Start polling for the token
            self._oauthPolling = True
            self.postLog(f"Device code obtained, polling for token (interval: {self._oauthPollInterval}s)")

        except urllib.error.HTTPError as ex:
            self.postLog(f"OAuth device code request failed: {ex.code} {ex.reason}")
            self.postUserNotification(_("Failed to start GitHub authentication"))
        except Exception as ex:
            self.postLog(f"OAuth device code request failed: {ex}")
            self.postUserNotification(_("Failed to start GitHub authentication"))

    def _pollOAuthToken(self):
        """Poll for OAuth token after user authorizes."""
        if not self._oauthPolling or not self._oauthDeviceCode:
            return

        # Check if expired
        if time.time() > self._oauthExpiresAt:
            self.postUserNotification(_("Authentication expired. Please try again."))
            self._resetOAuthState()
            return

        data = urllib.parse.urlencode({
            "client_id": GITHUB_CLIENT_ID,
            "device_code": self._oauthDeviceCode,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
        }).encode("utf-8")

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        try:
            req = urllib.request.Request(OAUTH_ACCESS_TOKEN_URL, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))

            error = result.get("error")
            if error == "authorization_pending":
                # User hasn't authorized yet, continue polling
                return
            elif error == "slow_down":
                # Increase poll interval
                self._oauthPollInterval = result.get("interval", self._oauthPollInterval + 5)
                self.postLog(f"Slowing down OAuth polling to {self._oauthPollInterval}s")
                return
            elif error == "expired_token":
                self.postUserNotification(_("Authentication expired. Please try again."))
                self._resetOAuthState()
                return
            elif error == "access_denied":
                self.postUserNotification(_("Authentication was denied."))
                self._resetOAuthState()
                return
            elif error:
                self.postLog(f"OAuth error: {error}")
                self.postUserNotification(_("Authentication failed: {error}").format(error=error))
                self._resetOAuthState()
                return

            # Success! We got a token
            accessToken = result.get("access_token")
            if accessToken:
                self._token = accessToken
                self._saveConfig()
                self._resetOAuthState()
                self.postUserNotification(_("GitHub authentication successful!"))
                self.postLog("OAuth authentication completed successfully")
                self._initializeService()

        except urllib.error.HTTPError as ex:
            self.postLog(f"OAuth token poll failed: {ex.code} {ex.reason}")
        except Exception as ex:
            self.postLog(f"OAuth token poll failed: {ex}")

    def _resetOAuthState(self):
        """Reset OAuth device flow state."""
        self._oauthDeviceCode = None
        self._oauthUserCode = None
        self._oauthPolling = False
        self._oauthExpiresAt = 0
        self._lastOAuthPoll = 0

    def _fetchPRComments(self, repo, prNumber):
        """Fetch all comments for a PR."""
        # Review comments (inline)
        reviewComments = self._apiRequest(f"/repos/{repo}/pulls/{prNumber}/comments") or []

        # Issue comments (general)
        issueComments = self._apiRequest(f"/repos/{repo}/issues/{prNumber}/comments") or []

        return {
            "review_comments": reviewComments,
            "issue_comments": issueComments
        }

    def _submitReview(self, repo, prNumber, event, body=None):
        """Submit a review (approve or request changes)."""
        data = {"event": event}
        if body:
            data["body"] = body

        result = self._apiRequest(f"/repos/{repo}/pulls/{prNumber}/reviews", method="POST", data=data)
        if result:
            self.postUserNotification(_("Review submitted"))
            # Refresh PR data
            self._refreshPRs()
        return result

    # ========== Dialogs ==========

    def _showTokenDialog(self):
        """Show dialog to enter GitHub token."""
        def showDialog():
            dlg = wx.TextEntryDialog(
                None,
                _("Enter your GitHub Personal Access Token:"),
                _("GitHub Token"),
                style=wx.OK | wx.CANCEL | wx.TE_PASSWORD
            )
            if dlg.ShowModal() == wx.ID_OK:
                token = dlg.GetValue().strip()
                if token:
                    self._token = token
                    self._saveConfig()
                    self.postUserNotification(_("Token saved"))
                    self._initializeService()
            dlg.Destroy()

        wx.CallAfter(showDialog)

    def _showAddRepoDialog(self):
        """Show dialog to add a repository."""
        def showDialog():
            dlg = wx.TextEntryDialog(
                None,
                _("Enter repository (owner/repo):"),
                _("Add Repository")
            )
            if dlg.ShowModal() == wx.ID_OK:
                repo = dlg.GetValue().strip()
                if repo and "/" in repo:
                    if repo not in self._repositories:
                        # Validate repo exists
                        result = self._apiRequest(f"/repos/{repo}")
                        if result:
                            self._repositories.append(repo)
                            self._saveConfig()
                            self.postUserNotification(_("Repository added: {repo}").format(repo=repo))
                            self._buildRepoMenu(repo)
                            self._updateSettingsMenu()
                        else:
                            self.postUserNotification(_("Repository not found or inaccessible"))
                    else:
                        self.postUserNotification(_("Repository already added"))
                else:
                    self.postUserNotification(_("Invalid format. Use owner/repo"))
            dlg.Destroy()

        wx.CallAfter(showDialog)

    def _showReviewDialog(self, repo, prNumber, reviewType):
        """Show dialog for review comment (optional)."""
        def showDialog():
            message = _("Enter review comment (optional):") if reviewType == "APPROVE" else _("Enter reason for requesting changes:")
            dlg = wx.TextEntryDialog(
                None,
                message,
                _("Review Comment"),
                style=wx.OK | wx.CANCEL | wx.TE_MULTILINE
            )
            if dlg.ShowModal() == wx.ID_OK:
                body = dlg.GetValue().strip() or None
                self._submitReview(repo, prNumber, reviewType, body)
            dlg.Destroy()

        wx.CallAfter(showDialog)

    # ========== Service Lifecycle ==========

    def execute(self):
        """Main service loop."""
        now = time.time()

        # Handle OAuth polling if in progress
        if self._oauthPolling:
            if now - self._lastOAuthPoll >= self._oauthPollInterval:
                self._lastOAuthPoll = now
                self._pollOAuthToken()
            return

        # Check if token is configured
        if not self._token:
            if not self.isAvailable() and not self._oauthPolling:
                self._startOAuthDeviceFlow()
            return

        # Initialize if not done
        if not self.isAvailable():
            self._initializeService()
            return

        # Periodic refresh
        if now - self._lastRefresh > self._refreshInterval:
            self._refreshPRs()

    def _initializeService(self):
        """Initialize the service after token is configured."""
        if not self._token:
            return

        # Verify token works
        user = self._apiRequest("/user")
        if not user:
            self.postUserNotification(_("Failed to authenticate with GitHub"))
            self._token = None
            return

        self.postLog(f"Authenticated as {user.get('login', 'unknown')}")
        self.enable()
        self.postReady()
        self._buildMenus()
        self._refreshPRs()

    def _refreshPRs(self):
        """Refresh PR data for all repositories."""
        self._lastRefresh = time.time()
        for repo in self._repositories:
            self._prs[repo] = self._fetchPRs(repo)
            self._updateRepoMenu(repo)

    # ========== Event Handlers ==========

    def on_menu_get_items(self, event, args):
        """Handle menu item requests."""
        menuId = args["id"]
        if menuId in self._menus:
            self.postMenuItemsList(self._menus[menuId])

    def on_menu_activate(self, event, args):
        """Handle menu item activation."""
        menuId = args.get("menuId")
        itemIdx = args.get("itemIdx")

        if menuId not in self._menus:
            return

        menu = self._menus[menuId]
        items = menu.get("items", [])
        if itemIdx < 0 or itemIdx >= len(items):
            return

        item = items[itemIdx]
        action = item.get("action")
        actionData = item.get("actionData", {})

        # Settings menu actions
        if action == "addRepo":
            self._showAddRepoDialog()
        elif action == "removeRepo":
            repo = actionData.get("repo")
            if repo in self._repositories:
                self._repositories.remove(repo)
                self._saveConfig()
                self._removeRepoMenu(repo)
                self._updateSettingsMenu()
                self.postUserNotification(_("Repository removed: {repo}").format(repo=repo))
        elif action == "startOAuth":
            self._startOAuthDeviceFlow()
        elif action == "signOut":
            self._token = None
            self._saveConfig()
            self.disable()
            self._updateSettingsMenu()
            self.postUserNotification(_("Signed out from GitHub"))
        elif action == "refreshAll":
            self._refreshPRs()
            self.postUserNotification(_("Refreshing PRs..."))

        # PR actions
        elif action == "openPR":
            url = actionData.get("url")
            if url:
                webbrowser.open(url)
                self.postUserNotification(_("Opening in browser"))
        elif action == "copyLink":
            url = actionData.get("url")
            if url:
                self._copyToClipboard(url)
                self.postUserNotification(_("Link copied"))
        elif action == "approvePR":
            repo = actionData.get("repo")
            prNumber = actionData.get("prNumber")
            self._showReviewDialog(repo, prNumber, "APPROVE")
        elif action == "requestChanges":
            repo = actionData.get("repo")
            prNumber = actionData.get("prNumber")
            self._showReviewDialog(repo, prNumber, "REQUEST_CHANGES")
        elif action == "viewComments":
            repo = actionData.get("repo")
            prNumber = actionData.get("prNumber")
            self._showPRComments(repo, prNumber)
        elif action == "speakComment":
            comment = actionData.get("comment")
            if comment:
                self.postUserNotification(comment)

    def _copyToClipboard(self, text):
        """Copy text to clipboard."""
        def doCopy():
            if wx.TheClipboard.Open():
                wx.TheClipboard.SetData(wx.TextDataObject(text))
                wx.TheClipboard.Close()
        wx.CallAfter(doCopy)

    # ========== Menu Building ==========

    def _buildMenus(self):
        """Build all menus."""
        # Settings menu (always first)
        self._settingsMenuId = self.addMenu(_("Settings"))
        self._updateSettingsMenu()

        # Build menus for each repository
        for repo in self._repositories:
            self._buildRepoMenu(repo)

    def _updateSettingsMenu(self):
        """Update the settings menu."""
        if self._settingsMenuId is None:
            return

        items = []

        # Add repository
        items.append({
            "name": _("Add repository"),
            "action": "addRepo",
            "actionData": {}
        })

        # Remove repository submenu items
        if self._repositories:
            items.append({"name": "---", "action": None})  # Separator
            for repo in self._repositories:
                items.append({
                    "name": _("Remove {repo}").format(repo=repo),
                    "action": "removeRepo",
                    "actionData": {"repo": repo}
                })

        items.append({"name": "---", "action": None})  # Separator

        # Sign in / Sign out
        if self._token:
            items.append({
                "name": _("Sign out"),
                "action": "signOut",
                "actionData": {}
            })
            items.append({
                "name": _("Re-authenticate with GitHub"),
                "action": "startOAuth",
                "actionData": {}
            })
        else:
            items.append({
                "name": _("Sign in with GitHub"),
                "action": "startOAuth",
                "actionData": {}
            })

        # Refresh all
        items.append({
            "name": _("Refresh all"),
            "action": "refreshAll",
            "actionData": {}
        })

        self._menus[self._settingsMenuId]["items"] = items
        self.postMenuUpdate()

    def _buildRepoMenu(self, repo):
        """Build menu for a repository."""
        if repo in self._repoMenuIds:
            return  # Already exists

        menuId = self.addMenu(repo)
        self._repoMenuIds[repo] = menuId
        self._prs[repo] = []
        self._updateRepoMenu(repo)

    def _removeRepoMenu(self, repo):
        """Remove menu for a repository."""
        if repo in self._repoMenuIds:
            menuId = self._repoMenuIds[repo]
            self.removeMenu(menuId)
            del self._repoMenuIds[repo]
            if repo in self._prs:
                del self._prs[repo]

    def _updateRepoMenu(self, repo):
        """Update the PR list for a repository."""
        menuId = self._repoMenuIds.get(repo)
        if menuId is None:
            return

        prs = self._prs.get(repo, [])
        items = []

        if not prs:
            items.append({
                "name": _("No open PRs"),
                "action": None,
                "actionData": {}
            })
        else:
            for pr in prs:
                # Build status indicators
                approvalIndicator = self._getApprovalIndicator(pr["approval_status"])
                ciIndicator = self._getCIIndicator(pr["ci_status"])
                draftIndicator = _("[Draft] ") if pr.get("draft") else ""

                # Format: [A][CI:OK] #123: Title (author)
                label = f"{draftIndicator}{approvalIndicator}{ciIndicator} #{pr['number']}: {pr['title']} ({pr['author']})"

                items.append({
                    "name": label,
                    "action": "openPRMenu",
                    "actionData": {"repo": repo, "pr": pr}
                })

        self._menus[menuId]["items"] = self._buildPRItems(repo, prs)
        self.postMenuUpdate()

    def _buildPRItems(self, repo, prs):
        """Build menu items for PRs with action submenus."""
        items = []

        if not prs:
            items.append({
                "name": _("No open PRs"),
                "action": None,
                "actionData": {}
            })
            return items

        for pr in prs:
            # Build status indicators
            approvalIndicator = self._getApprovalIndicator(pr["approval_status"])
            ciIndicator = self._getCIIndicator(pr["ci_status"])
            draftIndicator = _("[Draft] ") if pr.get("draft") else ""

            # PR header
            label = f"{draftIndicator}{approvalIndicator}{ciIndicator} #{pr['number']}: {pr['title']} ({pr['author']})"
            items.append({
                "name": label,
                "action": "openPR",
                "actionData": {"url": pr["url"]}
            })

            # Action items for this PR (indented)
            items.append({
                "name": f"  {_('Open in browser')}",
                "action": "openPR",
                "actionData": {"url": pr["url"]}
            })
            items.append({
                "name": f"  {_('Copy link')}",
                "action": "copyLink",
                "actionData": {"url": pr["url"]}
            })
            items.append({
                "name": f"  {_('Approve')}",
                "action": "approvePR",
                "actionData": {"repo": repo, "prNumber": pr["number"]}
            })
            items.append({
                "name": f"  {_('Request changes')}",
                "action": "requestChanges",
                "actionData": {"repo": repo, "prNumber": pr["number"]}
            })
            items.append({
                "name": f"  {_('View comments')}",
                "action": "viewComments",
                "actionData": {"repo": repo, "prNumber": pr["number"]}
            })

        return items

    def _showPRComments(self, repo, prNumber):
        """Fetch and display PR comments."""
        comments = self._fetchPRComments(repo, prNumber)

        # Build combined comment list
        allComments = []

        # Issue comments
        for c in comments.get("issue_comments", []):
            allComments.append({
                "type": "comment",
                "author": c["user"]["login"],
                "body": c["body"],
                "created_at": c["created_at"]
            })

        # Review comments (inline)
        for c in comments.get("review_comments", []):
            path = c.get("path", "")
            line = c.get("line") or c.get("original_line", "")
            allComments.append({
                "type": "inline",
                "author": c["user"]["login"],
                "body": c["body"],
                "path": path,
                "line": line,
                "created_at": c["created_at"]
            })

        # Sort by date
        allComments.sort(key=lambda x: x["created_at"])

        if not allComments:
            self.postUserNotification(_("No comments on this PR"))
            return

        # Announce comments
        self.postUserNotification(_("{count} comments").format(count=len(allComments)))

        # Build comment items for the current menu
        menuId = self._repoMenuIds.get(repo)
        if menuId is None:
            return

        # Create temporary comment menu
        items = [{
            "name": _("Back to PR list"),
            "action": "refreshAll",
            "actionData": {}
        }]

        for c in allComments:
            if c["type"] == "inline":
                prefix = f"[{c['path']}:{c['line']}] "
            else:
                prefix = ""

            # Truncate long comments
            body = c["body"][:100] + "..." if len(c["body"]) > 100 else c["body"]
            body = body.replace("\n", " ")

            label = f"{c['author']}: {prefix}{body}"
            items.append({
                "name": label,
                "action": "speakComment",
                "actionData": {"comment": f"{c['author']}: {c['body']}"}
            })

        self._menus[menuId]["items"] = items
        self.postMenuUpdate()

    def _getApprovalIndicator(self, status):
        """Get approval status indicator."""
        indicators = {
            "APPROVED": "[A]",
            "CHANGES_REQUESTED": "[R]",
            "PENDING": "[?]"
        }
        return indicators.get(status, "[?]")

    def _getCIIndicator(self, status):
        """Get CI status indicator."""
        indicators = {
            "SUCCESS": "[CI:OK]",
            "FAILURE": "[CI:!!]",
            "PENDING": "[CI:--]",
            "NONE": "",
            "MIXED": "[CI:~]",
            "UNKNOWN": "[CI:?]"
        }
        return indicators.get(status, "")
