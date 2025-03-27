# 911 Intel Discord Bot

A powerful Discord bot powered by Google Gemini AI, designed by kelpyshades.

## Overview

911 Intel is an advanced AI assistant for Discord that leverages Google's Gemini AI models to provide intelligent responses and analyze media. The bot maintains conversation context across interactions and provides a seamless experience for users.

## Features

- **AI Conversations**: Have natural conversations with Google's Gemini 1.5 Pro AI
- **Media Analysis**: Analyze images, videos, and audio files using Gemini 1.5 Flash
- **Persistent Memory**: Conversations are maintained for each user (with weekly reset)
- **Rate Limiting**: Prevents abuse with per-user and global rate limits
- **Discord Embeds**: All responses are formatted in beautiful Discord embeds

## Setup Instructions

### Prerequisites

- Python 3.8 or higher
- A Discord bot token (from [Discord Developer Portal](https://discord.com/developers/applications))
- A Google Gemini API key (from [Google AI Studio](https://makersuite.google.com/app/apikey))

### Installation

1. Clone this repository:

   ```
   git clone https://github.com/kelpyshades/911-intel-bot.git
   cd 911-intel-bot
   ```

2. Install required packages:

   ```
   pip install -r requirements.txt
   ```

3. Create a `.env` file in the project root with the following content:

   ```
   DISCORD_BOT_TOKEN=your_discord_bot_token
   GEMINI_API_KEY=your_gemini_api_key
   ```

4. Run the bot:
   ```
   python bot.py
   ```

## Commands

- `>ask [question]` - Ask the AI a question
- `>image` - Analyze an attached image (must include an attachment)
- `>video` - Analyze an attached video (must include an attachment)
- `>audio` - Analyze an attached audio file (must include an attachment)
- `>forget [user|all]` - Reset your conversation history or all conversations (admin only)
- `>status` - Check the bot's status and API connectivity
- `>expiry` - Check when your conversation will automatically reset
- `>help` - Show all available commands

You can also mention the bot directly (`@911 Intel`) with a question to get a response.

## Conversation Management

- Each user has their own conversation context that persists across channels
- Conversations automatically reset after 7 days
- Users can manually reset their own conversation with `>forget`
- Server administrators can reset all conversations with `>forget all`

## Environment Variables

| Variable            | Description                                                |
| ------------------- | ---------------------------------------------------------- |
| `DISCORD_BOT_TOKEN` | Your Discord bot's token from the Discord Developer Portal |
| `GEMINI_API_KEY`    | Your Google Gemini API key                                 |

## Deployment

### Using PM2 (recommended for 24/7 operation)

1. Install PM2:

   ```
   npm install -g pm2
   ```

2. Start the bot with PM2:

   ```
   pm2 start bot.py --name "911-intel-bot" --interpreter python3
   ```

3. Configure PM2 to start on system boot:
   ```
   pm2 startup
   pm2 save
   ```

### Using Railway or Heroku

1. Create a `Procfile` with:

   ```
   worker: python bot.py
   ```

2. Set environment variables in the platform dashboard
3. Deploy your application

## Security Considerations

- Never share your `.env` file or API keys
- Regularly rotate your API keys
- Consider implementing additional permission checks for sensitive commands
- Monitor your bot's usage to stay within API limits

## Credits

- **Creator**: kelpyshades
- **AI**: Google Gemini 1.5 Pro & Flash
- **Discord API**: discord.py

## License

This project is licensed under the MIT License - see the LICENSE file for details.
