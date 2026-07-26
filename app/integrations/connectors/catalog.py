from __future__ import annotations

from app.integrations.base import DeterministicMockConnector
from app.integrations.schemas import (
    ApprovalPolicy,
    ConnectorOperation,
    IntegrationCapability,
    IntegrationPermission,
    IntegrationProvider,
)


def _operation(
    name: str,
    *,
    write: bool = False,
    sensitive: bool = False,
    approval: ApprovalPolicy = ApprovalPolicy.NONE,
    admin: bool = False,
) -> ConnectorOperation:
    permissions = {
        IntegrationPermission.WRITE
        if write
        else IntegrationPermission.READ
    }
    if sensitive:
        permissions.add(IntegrationPermission.SENSITIVE_DATA)
    if approval is not ApprovalPolicy.NONE:
        permissions.add(IntegrationPermission.APPROVAL_REQUIRED)
    if admin:
        permissions.add(IntegrationPermission.ADMIN)
    return ConnectorOperation(
        name=name,
        description=f"Mock {name.replace('_', ' ')} operation.",
        permissions=frozenset(permissions),
        approval_policy=approval,
        sensitive_data=sensitive,
    )


MAIL_OPERATIONS = (
    _operation("read_email", sensitive=True),
    _operation("search_email", sensitive=True),
    _operation("draft_email", write=True, sensitive=True),
    _operation(
        "send_email",
        write=True,
        sensitive=True,
        approval=ApprovalPolicy.USER,
    ),
)
CALENDAR_OPERATIONS = (
    _operation("list_events", sensitive=True),
    _operation(
        "create_event", write=True, approval=ApprovalPolicy.USER
    ),
    _operation(
        "update_event", write=True, approval=ApprovalPolicy.USER
    ),
    _operation(
        "delete_event",
        write=True,
        approval=ApprovalPolicy.ADMIN,
        admin=True,
    ),
)
MESSAGE_OPERATIONS = (
    _operation("list_channels"),
    _operation("read_messages", sensitive=True),
    _operation("search_messages", sensitive=True),
    _operation(
        "send_message",
        write=True,
        sensitive=True,
        approval=ApprovalPolicy.USER,
    ),
)


class GoogleMailConnector(DeterministicMockConnector):
    name = "google_mail"
    description = "Deterministic Google Mail architecture adapter."
    provider_name = IntegrationProvider.GOOGLE
    operations = MAIL_OPERATIONS
    connector_capabilities = frozenset(
        {
            IntegrationCapability.READ,
            IntegrationCapability.WRITE,
            IntegrationCapability.SEARCH,
            IntegrationCapability.MESSAGING,
        }
    )


class GoogleCalendarConnector(DeterministicMockConnector):
    name = "google_calendar"
    description = "Deterministic Google Calendar architecture adapter."
    provider_name = IntegrationProvider.GOOGLE
    operations = CALENDAR_OPERATIONS
    connector_capabilities = frozenset(
        {
            IntegrationCapability.READ,
            IntegrationCapability.WRITE,
            IntegrationCapability.CALENDAR,
        }
    )


class GoogleContactsConnector(DeterministicMockConnector):
    name = "google_contacts"
    description = "Deterministic Google Contacts architecture adapter."
    provider_name = IntegrationProvider.GOOGLE
    operations = (
        _operation("search_contact", sensitive=True),
        _operation("list_contacts", sensitive=True),
        _operation("create_contact", write=True, sensitive=True),
        _operation("update_contact", write=True, sensitive=True),
    )
    connector_capabilities = frozenset(
        {
            IntegrationCapability.READ,
            IntegrationCapability.WRITE,
            IntegrationCapability.SEARCH,
            IntegrationCapability.CONTACTS,
        }
    )


class GoogleDriveConnector(DeterministicMockConnector):
    name = "google_drive"
    description = "Deterministic Google Drive architecture adapter."
    provider_name = IntegrationProvider.GOOGLE
    operations = (
        _operation("search_files", sensitive=True),
        _operation("list_files", sensitive=True),
        _operation("get_file_metadata", sensitive=True),
        _operation("create_folder", write=True, sensitive=True),
    )
    connector_capabilities = frozenset(
        {
            IntegrationCapability.READ,
            IntegrationCapability.WRITE,
            IntegrationCapability.SEARCH,
            IntegrationCapability.FILES,
        }
    )


class OutlookConnector(DeterministicMockConnector):
    name = "outlook"
    description = "Deterministic Outlook architecture adapter."
    provider_name = IntegrationProvider.MICROSOFT
    operations = MAIL_OPERATIONS
    connector_capabilities = GoogleMailConnector.connector_capabilities


class TeamsConnector(DeterministicMockConnector):
    name = "teams"
    description = "Deterministic Microsoft Teams architecture adapter."
    provider_name = IntegrationProvider.MICROSOFT
    operations = (
        _operation("list_teams"),
        _operation("list_channels"),
        _operation("read_messages", sensitive=True),
        _operation(
            "send_message",
            write=True,
            sensitive=True,
            approval=ApprovalPolicy.USER,
        ),
    )
    connector_capabilities = frozenset(
        {
            IntegrationCapability.READ,
            IntegrationCapability.WRITE,
            IntegrationCapability.MESSAGING,
            IntegrationCapability.COLLABORATION,
        }
    )


class OfficeCalendarConnector(DeterministicMockConnector):
    name = "office_calendar"
    description = "Deterministic Office Calendar architecture adapter."
    provider_name = IntegrationProvider.MICROSOFT
    operations = CALENDAR_OPERATIONS
    connector_capabilities = GoogleCalendarConnector.connector_capabilities


class SlackConnector(DeterministicMockConnector):
    name = "slack"
    description = "Deterministic Slack architecture adapter."
    provider_name = IntegrationProvider.SLACK
    operations = MESSAGE_OPERATIONS
    connector_capabilities = frozenset(
        {
            IntegrationCapability.READ,
            IntegrationCapability.WRITE,
            IntegrationCapability.SEARCH,
            IntegrationCapability.MESSAGING,
            IntegrationCapability.COLLABORATION,
        }
    )


class DiscordConnector(DeterministicMockConnector):
    name = "discord"
    description = "Deterministic Discord architecture adapter."
    provider_name = IntegrationProvider.DISCORD
    operations = MESSAGE_OPERATIONS
    connector_capabilities = SlackConnector.connector_capabilities


class NotionConnector(DeterministicMockConnector):
    name = "notion"
    description = "Deterministic Notion architecture adapter."
    provider_name = IntegrationProvider.NOTION
    operations = (
        _operation("search_pages", sensitive=True),
        _operation("get_page", sensitive=True),
        _operation("create_page", write=True, sensitive=True),
        _operation("update_page", write=True, sensitive=True),
    )
    connector_capabilities = frozenset(
        {
            IntegrationCapability.READ,
            IntegrationCapability.WRITE,
            IntegrationCapability.SEARCH,
            IntegrationCapability.DATA,
        }
    )


class AirtableConnector(DeterministicMockConnector):
    name = "airtable"
    description = "Deterministic Airtable architecture adapter."
    provider_name = IntegrationProvider.AIRTABLE
    operations = (
        _operation("list_records", sensitive=True),
        _operation("get_record", sensitive=True),
        _operation("create_record", write=True, sensitive=True),
        _operation("update_record", write=True, sensitive=True),
    )
    connector_capabilities = frozenset(
        {
            IntegrationCapability.READ,
            IntegrationCapability.WRITE,
            IntegrationCapability.DATA,
        }
    )


class GitHubConnector(DeterministicMockConnector):
    name = "github"
    description = "Deterministic GitHub architecture adapter."
    provider_name = IntegrationProvider.GITHUB
    operations = (
        _operation("list_repositories"),
        _operation("list_pull_requests"),
        _operation("get_issue"),
        _operation(
            "create_issue",
            write=True,
            approval=ApprovalPolicy.USER,
        ),
    )
    connector_capabilities = frozenset(
        {
            IntegrationCapability.READ,
            IntegrationCapability.WRITE,
            IntegrationCapability.COLLABORATION,
        }
    )


class GitLabConnector(DeterministicMockConnector):
    name = "gitlab"
    description = "Deterministic GitLab architecture adapter."
    provider_name = IntegrationProvider.GITLAB
    operations = (
        _operation("list_projects"),
        _operation("list_merge_requests"),
        _operation("get_issue"),
        _operation(
            "create_issue",
            write=True,
            approval=ApprovalPolicy.USER,
        ),
    )
    connector_capabilities = GitHubConnector.connector_capabilities


class SearchConnector(DeterministicMockConnector):
    name = "search"
    description = "Deterministic web search architecture adapter."
    provider_name = IntegrationProvider.WEB
    operations = (
        _operation("web_search"),
        _operation("image_search"),
    )
    connector_capabilities = frozenset(
        {IntegrationCapability.READ, IntegrationCapability.SEARCH}
    )


class NewsConnector(DeterministicMockConnector):
    name = "news"
    description = "Deterministic news architecture adapter."
    provider_name = IntegrationProvider.WEB
    operations = (
        _operation("search_news"),
        _operation("latest_headlines"),
    )
    connector_capabilities = frozenset(
        {IntegrationCapability.READ, IntegrationCapability.SEARCH}
    )


class WeatherConnector(DeterministicMockConnector):
    name = "weather"
    description = "Deterministic weather architecture adapter."
    provider_name = IntegrationProvider.WEB
    operations = (
        _operation("current_weather"),
        _operation("weather_forecast"),
    )
    connector_capabilities = frozenset({IntegrationCapability.READ})


class MarketDataConnector(DeterministicMockConnector):
    name = "market_data"
    description = "Deterministic market data architecture adapter."
    provider_name = IntegrationProvider.FINANCE
    operations = (
        _operation("latest_price"),
        _operation("historical_data"),
        _operation("market_summary"),
    )
    connector_capabilities = frozenset(
        {IntegrationCapability.READ, IntegrationCapability.MARKET_DATA}
    )


class EconomicCalendarConnector(DeterministicMockConnector):
    name = "economic_calendar"
    description = "Deterministic economic calendar architecture adapter."
    provider_name = IntegrationProvider.FINANCE
    operations = (
        _operation("economic_events"),
        _operation("calendar_range"),
    )
    connector_capabilities = frozenset(
        {
            IntegrationCapability.READ,
            IntegrationCapability.CALENDAR,
            IntegrationCapability.MARKET_DATA,
        }
    )


MOCK_CONNECTOR_TYPES: tuple[
    type[DeterministicMockConnector], ...
] = (
    GoogleMailConnector,
    GoogleCalendarConnector,
    GoogleContactsConnector,
    GoogleDriveConnector,
    OutlookConnector,
    TeamsConnector,
    OfficeCalendarConnector,
    SlackConnector,
    DiscordConnector,
    NotionConnector,
    AirtableConnector,
    GitHubConnector,
    GitLabConnector,
    SearchConnector,
    NewsConnector,
    WeatherConnector,
    MarketDataConnector,
    EconomicCalendarConnector,
)
