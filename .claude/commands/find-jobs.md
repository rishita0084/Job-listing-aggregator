Search for jobs matching: $ARGUMENTS

Use the Job Alert MCP server to search and filter job listings.
Parse $ARGUMENTS to extract:
- Job role/keyword (required)
- Experience requirements (e.g., "3+ years experience", optional)
- Time window (e.g., "last 24 hours", "last week", optional - defaults to week)
- Recipient email (optional - if not provided, uses default from configuration)

Examples:
- "software engineer" → searches for software engineer jobs (default time week)
- "software engineer 3+ years experience" → software engineer roles requiring 3+ years experience
- "data scientist last 24 hours" → data scientist jobs from last 24 hours
- "product manager 2+ years experience last week" → PM roles with 2+ years exp from last week
- "developer to john@example.com" → send results to john@example.com

The command will:
1. Extract parameters from $ARGUMENTS
2. Call the MCP server's search_jobs tool with appropriate query and hours_window
3. Receive filtered results from the MCP server (which handles experience matching and time filtering)
4. Optionally send results via email using the MCP server's send_email tool
5. Return results in a structured format for presentation

Note: Location filtering is not available as the source platforms (RemoteOK, WeWorkRemotely) focus on remote-first global positions. The MCP server handles experience assessment using transparent text heuristics.