import os
import time
import logging
import asyncio
import discord
from discord.ext import commands
import google.generativeai as genai
from dotenv import load_dotenv
from collections import defaultdict
import json
import aiohttp
from urllib.parse import quote_plus
from datetime import datetime, timedelta
from discord.ext import tasks

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("gemini-bot")

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# Configure the Gemini API
genai.configure(api_key=GEMINI_API_KEY)

# Create models
text_model = genai.GenerativeModel('gemini-1.5-pro')  # For text conversations
vision_model = genai.GenerativeModel('gemini-1.5-flash', generation_config={
    'max_output_tokens': 2048,  # Limit token output for vision responses
    'temperature': 0.4  # Lower temperature for more concise responses
})  # For images/video/audio

# Set up Discord bot with intents
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='>', intents=intents)

# Bot default personality template
bot_template = [
    {'role': 'user', 'parts': ["Hi!"]},
    {'role': 'model', 'parts': ["Hello! I am 911 Intel, an AI assistant designed by kelpyshades!"]},
    {'role': 'user', 'parts': ["Please give helpful and concise answers."]},
    {'role': 'model', 'parts': ["I'll do my best to provide you with accurate and concise information. How can I assist you today?"]},
]

# Dictionary to store conversation history by channel_id + user_id
conversations = {}

# Rate limiting configuration
class RateLimiter:
    def __init__(self, max_calls, time_frame):
        self.max_calls = max_calls
        self.time_frame = time_frame  # in seconds
        self.calls = defaultdict(list)
    
    async def add_call(self, key):
        """Add a call to the tracker and return True if rate limited"""
        current_time = time.time()
        
        # Remove calls older than the time frame
        self.calls[key] = [call_time for call_time in self.calls[key] 
                          if current_time - call_time < self.time_frame]
        
        # Check if rate limited
        if len(self.calls[key]) >= self.max_calls:
            return True
        
        # Add the new call
        self.calls[key].append(current_time)
        return False
    
    async def get_retry_after(self, key):
        """Get the time to wait before retrying in seconds"""
        if not self.calls[key]:
            return 0
        
        oldest_call = min(self.calls[key])
        return max(0, self.time_frame - (time.time() - oldest_call))

# Create rate limiters
user_limiter = RateLimiter(max_calls=5, time_frame=60)  # 5 calls per minute per user
global_limiter = RateLimiter(max_calls=30, time_frame=60)  # 30 calls per minute globally

# Discord Embed Color Guide
COLORS = {
    "blue": 0x4285F4,      # Google Blue - Standard responses
    "red": 0xFF0000,       # Error messages
    "yellow": 0xFFCC00,    # Warnings and rate limits
    "green": 0x34A853,     # Success messages
    "teal": 0x00C09A,      # Status and info messages
    "purple": 0xA142F4,    # Special commands
    "orange": 0xFB8C00     # Search results
}

@bot.event
async def on_ready():
    logger.info(f'{bot.user} has connected to Discord!')
    check_conversation_age.start()
    # Set a custom status activity
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching, 
        name="for intel requests | >help"
    ))

# Helper function to get a conversation for a specific user (channel-agnostic)
def get_conversation(user_id):
    # We no longer need channel_id in the key
    conversation_key = f"user:{user_id}"
    
    if conversation_key not in conversations:
        try:
            # Create new conversation with timestamp
            conversations[conversation_key] = {
                "chat": text_model.start_chat(history=bot_template),
                "created_at": datetime.now()
            }
            logger.info(f"Created new conversation for user {user_id}")
        except Exception as e:
            logger.error(f"Error creating conversation: {str(e)}")
            raise
    
    # Return just the chat object from our dictionary
    return conversations[conversation_key]["chat"]

# Helper function for Gemini API interaction with error handling
async def get_gemini_response(conversation, question):
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            response = conversation.send_message(question)
            return response.text
        except genai.types.generation_types.BlockedPromptException:
            logger.warning(f"Content blocked by safety settings: {question[:50]}...")
            return "I'm sorry, but I cannot respond to that request as it may violate content safety guidelines."
        except Exception as e:
            logger.error(f"Attempt {attempt+1}/{max_retries} failed: {str(e)}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                return f"I'm experiencing technical difficulties. Please try again later. Error: {str(e)}"

@bot.command(name='ask')
async def ask(ctx, *, question):
    """Ask a question to Gemini AI"""
    user_id = str(ctx.author.id)
    channel_id = str(ctx.channel.id)
    
    # Check rate limits
    is_user_limited = await user_limiter.add_call(user_id)
    is_global_limited = await global_limiter.add_call("global")
    
    if is_user_limited:
        retry_after = await user_limiter.get_retry_after(user_id)
        embed = discord.Embed(
            title="⚠️ Rate Limit Reached",
            description=f"You're sending messages too quickly! Please wait {retry_after:.1f} seconds before trying again.",
            color=COLORS["yellow"]
        )
        await ctx.send(embed=embed)
        return
    
    if is_global_limited:
        retry_after = await global_limiter.get_retry_after("global")
        embed = discord.Embed(
            title="⚠️ Global Rate Limit Reached",
            description=f"The bot is handling too many requests right now. Please try again in {retry_after:.1f} seconds.",
            color=COLORS["yellow"]
        )
        await ctx.send(embed=embed)
        return
    
    # Send "typing" indicator
    async with ctx.typing():
        try:
            # Get the channel-specific conversation for this user
            conversation = get_conversation(user_id)
            
            # Create initial "processing" embed
            processing_embed = discord.Embed(
                title="💭 Processing your question...",
                description=f"Question: {question}",
                color=COLORS["blue"]
            )
            processing_message = await ctx.send(embed=processing_embed)
            
            # Get response from Gemini
            response_text = await get_gemini_response(conversation, question)
            
            # Check response length to determine how to format
            if len(response_text) > 4000:  # Discord embeds have a 4096 character limit for description
                # Split into chunks for long responses
                chunks = [response_text[i:i+4000] for i in range(0, len(response_text), 4000)]
                
                # Create initial embed
                embed = discord.Embed(
                    title=f"🤖 Response (1/{len(chunks)})",
                    description=chunks[0],
                    color=COLORS["blue"]
                )
                
                # Edit the processing message with the first part
                await processing_message.edit(embed=embed)
                
                # Send additional chunks as separate embeds
                for i, chunk in enumerate(chunks[1:], 2):
                    embed = discord.Embed(
                        title=f"🤖 Response (continued {i}/{len(chunks)})",
                        description=chunk,
                        color=COLORS["blue"]
                    )
                    await ctx.send(embed=embed)
            else:
                # Create single embed for the response
                embed = discord.Embed(
                    title="🤖 Gemini Response",
                    description=response_text,
                    color=COLORS["blue"]
                )
                
                # Add metadata footer
                embed.set_footer(text=f"Requested by {ctx.author.display_name} | 911 Intel | Designed by kelpyshades")
                embed.timestamp = ctx.message.created_at
                
                # Edit the processing message with the response
                await processing_message.edit(embed=embed)
                
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            error_embed = discord.Embed(
                title="❌ Error",
                description=f"Sorry, I encountered an error: {str(e)}",
                color=COLORS["red"]
            )
            await ctx.send(embed=error_embed)

@bot.command(name='forget')
async def forget(ctx, target="user"):
    """Reset the conversation history.
    Usage: >forget [user|all]
    - user: Clear your conversation (default)
    - all: Clear all conversations (admin only)
    """
    user_id = str(ctx.author.id)
    
    keys_to_remove = []
    conversation_key = f"user:{user_id}"
    
    if target.lower() == "user":
        # Remove just this user's conversation
        keys_to_remove = [conversation_key]
        target_description = "your conversation history"
    elif target.lower() == "all" and ctx.author.guild_permissions.administrator:
        # Remove all conversations (admin only)
        keys_to_remove = list(conversations.keys())
        target_description = "all conversation histories"
    elif target.lower() == "all":
        embed = discord.Embed(
            title="❌ Permission Denied",
            description="Only administrators can clear all conversations.",
            color=COLORS["red"]
        )
        await ctx.send(embed=embed)
        return
    
    for key in keys_to_remove:
        if key in conversations:
            del conversations[key]
    
    embed = discord.Embed(
        title="🧹 Memory Reset",
        color=COLORS["teal"]
    )
    
    if keys_to_remove:
        embed.description = f"I've cleared {len(keys_to_remove)} conversation(s)!"
        embed.add_field(name="Details", value=f"Reset: {target_description}", inline=False)
    else:
        embed.description = "No conversations were found to clear."
    
    embed.set_footer(text=f"Requested by {ctx.author.display_name}")
    embed.timestamp = ctx.message.created_at
    
    await ctx.send(embed=embed)

@bot.event
async def on_message(message):
    # Don't respond to our own messages
    if message.author == bot.user:
        return
    
    # Process commands first
    await bot.process_commands(message)
    
    # Check if the bot was mentioned
    if bot.user in message.mentions:
        user_id = str(message.author.id)
        channel_id = str(message.channel.id)
        
        # Check rate limits
        is_user_limited = await user_limiter.add_call(user_id)
        is_global_limited = await global_limiter.add_call("global")
        
        if is_user_limited:
            retry_after = await user_limiter.get_retry_after(user_id)
            embed = discord.Embed(
                title="⚠️ Rate Limit Reached",
                description=f"You're sending messages too quickly! Please wait {retry_after:.1f} seconds before trying again.",
                color=COLORS["yellow"]
            )
            await message.channel.send(embed=embed)
            return
        
        if is_global_limited:
            retry_after = await global_limiter.get_retry_after("global")
            embed = discord.Embed(
                title="⚠️ Global Rate Limit Reached",
                description=f"The bot is handling too many requests right now. Please try again in {retry_after:.1f} seconds.",
                color=COLORS["yellow"]
            )
            await message.channel.send(embed=embed)
            return
        
        # Remove the mention from the message
        question = message.content.replace(f'<@{bot.user.id}>', '').strip()
        
        # Improved introduction if no specific question is asked
        if not question:
            embed = discord.Embed(
                title="👋 Hello there!",
                description=(
                    f"I'm **911 Intel**, an advanced AI assistant designed by **kelpyshades** and powered by **Google Gemini**!\n\n"
                    f"My Gemini AI brain allows me to answer questions, analyze images, and search the web for information.\n\n"
                    f"Try commands like `>ask`, `>image`, or `>search` to see what Gemini can do.\n\n"
                    f"What intelligence can I gather for you today, {message.author.display_name}?"
                ),
                color=COLORS["blue"]
            )
            # Add bot info footer
            embed.set_footer(text="911 Intel | Designed by kelpyshades | Powered by Google Gemini 1.5")
            await message.channel.send(embed=embed)
            return
        
        # Send "typing" indicator
        async with message.channel.typing():
            try:
                # Create processing embed
                processing_embed = discord.Embed(
                    title="💭 Processing your question...",
                    description=f"Question: {question}",
                    color=COLORS["blue"]
                )
                processing_message = await message.channel.send(embed=processing_embed)
                
                # Get conversation specific to this channel+user
                conversation = get_conversation(user_id)
                
                # Get response from Gemini
                response_text = await get_gemini_response(conversation, question)
                
                # Check response length to determine how to format
                if len(response_text) > 4000:
                    chunks = [response_text[i:i+4000] for i in range(0, len(response_text), 4000)]
                    
                    # Create initial embed
                    embed = discord.Embed(
                        title=f"🤖 Response (1/{len(chunks)})",
                        description=chunks[0],
                        color=COLORS["blue"]
                    )
                    
                    # Edit the processing message with the first part
                    await processing_message.edit(embed=embed)
                    
                    # Send additional chunks as separate embeds
                    for i, chunk in enumerate(chunks[1:], 2):
                        embed = discord.Embed(
                            title=f"🤖 Response (continued {i}/{len(chunks)})",
                            description=chunk,
                            color=COLORS["blue"]
                        )
                        await message.channel.send(embed=embed)
                else:
                    # Create single embed for the response
                    embed = discord.Embed(
                        title="🤖 Gemini Response",
                        description=response_text,
                        color=COLORS["blue"]
                    )
                    
                    # Add metadata footer
                    embed.set_footer(text=f"Requested by {message.author.display_name} | 911 Intel | Designed by kelpyshades")
                    embed.timestamp = message.created_at
                    
                    # Edit the processing message with the response
                    await processing_message.edit(embed=embed)
                    
            except Exception as e:
                logger.error(f"Error processing mention: {str(e)}")
                error_embed = discord.Embed(
                    title="❌ Error",
                    description=f"Sorry, I encountered an error: {str(e)}",
                    color=COLORS["red"]
                )
                await message.channel.send(embed=error_embed)

@bot.command(name='image')
async def process_image(ctx):
    """Process an image with Gemini 1.5 Flash"""
    # Check if an image is attached
    if not ctx.message.attachments or not ctx.message.attachments[0].content_type.startswith('image/'):
        embed = discord.Embed(
            title="❌ Missing Image",
            description="Please attach an image to analyze.",
            color=COLORS["red"]
        )
        await ctx.send(embed=embed)
        return
    
    # Check rate limits
    user_id = str(ctx.author.id)
    is_user_limited = await user_limiter.add_call(user_id)
    is_global_limited = await global_limiter.add_call("global")
    
    if is_user_limited or is_global_limited:
        retry_after = max(
            await user_limiter.get_retry_after(user_id),
            await global_limiter.get_retry_after("global")
        )
        embed = discord.Embed(
            title="⚠️ Rate Limit Reached",
            description=f"Rate limit reached. Please try again in {retry_after:.1f} seconds.",
            color=COLORS["yellow"]
        )
        await ctx.send(embed=embed)
        return
    
    attachment = ctx.message.attachments[0]
    
    # Send "typing" indicator
    async with ctx.typing():
        try:
            # Create processing embed
            processing_embed = discord.Embed(
                title="🔍 Gemini is analyzing your image...",
                description="Please wait while Gemini processes the image.",
                color=COLORS["blue"]
            )
            processing_embed.set_image(url=attachment.url)
            processing_message = await ctx.send(embed=processing_embed)
            
            # Download the image
            image_data = await attachment.read()
            
            # Generate a response based on the image using gemini-1.5-flash
            response = vision_model.generate_content([
                "Describe what you see in this image briefly but accurately. Keep your response under 2000 characters.",
                image_data
            ], generation_config={'max_output_tokens': 1024})
            
            # Create response embed
            embed = discord.Embed(
                title="🖼️ Gemini Image Analysis",
                description=response.text,
                color=COLORS["blue"]
            )
            
            # Keep the image in the response
            embed.set_image(url=attachment.url)
            
            # Add metadata
            embed.set_footer(text=f"Requested by {ctx.author.display_name} | 911 Intel | Designed by kelpyshades")
            embed.timestamp = ctx.message.created_at
            
            # Edit the processing message with the response
            await processing_message.edit(embed=embed)
            
        except Exception as e:
            logger.error(f"Error processing image: {str(e)}")
            error_embed = discord.Embed(
                title="❌ Error Processing Image",
                description=f"Error processing image: {str(e)}",
                color=COLORS["red"]
            )
            await processing_message.edit(embed=error_embed)

@bot.command(name='video')
async def process_video(ctx):
    """Process a video with Gemini 1.5 Flash"""
    # Check if a video is attached
    if not ctx.message.attachments or not any(attachment.content_type and 'video/' in attachment.content_type for attachment in ctx.message.attachments):
        embed = discord.Embed(
            title="❌ Missing Video",
            description="Please attach a video to analyze.",
            color=COLORS["red"]
        )
        await ctx.send(embed=embed)
        return
    
    # Find the video attachment
    video_attachment = next(attachment for attachment in ctx.message.attachments if 'video/' in attachment.content_type)
    
    # Check rate limits
    user_id = str(ctx.author.id)
    is_user_limited = await user_limiter.add_call(user_id)
    is_global_limited = await global_limiter.add_call("global")
    
    if is_user_limited or is_global_limited:
        retry_after = max(
            await user_limiter.get_retry_after(user_id),
            await global_limiter.get_retry_after("global")
        )
        embed = discord.Embed(
            title="⚠️ Rate Limit Reached",
            description=f"Rate limit reached. Please try again in {retry_after:.1f} seconds.",
            color=COLORS["yellow"]
        )
        await ctx.send(embed=embed)
        return
    
    # Send "typing" indicator
    async with ctx.typing():
        try:
            # Create processing embed
            processing_embed = discord.Embed(
                title="🎬 Analyzing video...",
                description="Please wait while I process the video.",
                color=COLORS["blue"]
            )
            processing_message = await ctx.send(embed=processing_embed)
            
            # Download the video
            video_data = await video_attachment.read()
            
            # Generate a response based on the video using gemini-1.5-flash
            response = vision_model.generate_content([
                "Describe what's happening in this video briefly but accurately. Keep your response under 2000 characters.",
                video_data
            ], generation_config={'max_output_tokens': 1024})
            
            # Create response embed
            embed = discord.Embed(
                title="🎬 Video Analysis",
                description=response.text,
                color=COLORS["blue"]
            )
            
            # Add metadata
            embed.set_footer(text=f"Requested by {ctx.author.display_name} | 911 Intel | Designed by kelpyshades")
            embed.timestamp = ctx.message.created_at
            
            # Edit the processing message with the response
            await processing_message.edit(embed=embed)
            
        except Exception as e:
            logger.error(f"Error processing video: {str(e)}")
            error_embed = discord.Embed(
                title="❌ Error Processing Video",
                description=f"Error processing video: {str(e)}",
                color=COLORS["red"]
            )
            await ctx.send(embed=error_embed)

@bot.command(name='audio')
async def process_audio(ctx):
    """Process an audio file with Gemini 1.5 Flash"""
    # Check if an audio file is attached
    if not ctx.message.attachments or not any(attachment.content_type and ('audio/' in attachment.content_type or attachment.filename.endswith(('.mp3', '.wav', '.ogg', '.m4a'))) for attachment in ctx.message.attachments):
        embed = discord.Embed(
            title="❌ Missing Audio",
            description="Please attach an audio file to analyze.",
            color=COLORS["red"]
        )
        await ctx.send(embed=embed)
        return
    
    # Find the audio attachment
    audio_attachment = next(attachment for attachment in ctx.message.attachments 
                          if attachment.content_type and 'audio/' in attachment.content_type 
                          or attachment.filename.endswith(('.mp3', '.wav', '.ogg', '.m4a')))
    
    # Check rate limits
    user_id = str(ctx.author.id)
    is_user_limited = await user_limiter.add_call(user_id)
    is_global_limited = await global_limiter.add_call("global")
    
    if is_user_limited or is_global_limited:
        retry_after = max(
            await user_limiter.get_retry_after(user_id),
            await global_limiter.get_retry_after("global")
        )
        embed = discord.Embed(
            title="⚠️ Rate Limit Reached",
            description=f"Rate limit reached. Please try again in {retry_after:.1f} seconds.",
            color=COLORS["yellow"]
        )
        await ctx.send(embed=embed)
        return
    
    # Send "typing" indicator
    async with ctx.typing():
        try:
            # Create processing embed
            processing_embed = discord.Embed(
                title="🎵 Analyzing audio...",
                description="Please wait while I process the audio file.",
                color=COLORS["blue"]
            )
            processing_message = await ctx.send(embed=processing_embed)
            
            # Download the audio
            audio_data = await audio_attachment.read()
            
            # Generate a response based on the audio using gemini-1.5-flash
            response = vision_model.generate_content([
                "Transcribe and analyze this audio content briefly. Keep your response under 2000 characters.",
                audio_data
            ], generation_config={'max_output_tokens': 1024})
            
            # Create response embed
            embed = discord.Embed(
                title="🎵 Audio Analysis",
                description=response.text,
                color=COLORS["blue"]
            )
            
            # Add metadata
            embed.set_footer(text=f"Requested by {ctx.author.display_name} | 911 Intel | Designed by kelpyshades")
            embed.timestamp = ctx.message.created_at
            
            # Edit the processing message with the response
            await processing_message.edit(embed=embed)
            
        except Exception as e:
            logger.error(f"Error processing audio: {str(e)}")
            error_embed = discord.Embed(
                title="❌ Error Processing Audio",
                description=f"Error processing audio: {str(e)}",
                color=COLORS["red"]
            )
            await ctx.send(embed=error_embed)

@bot.event
async def on_command_error(ctx, error):
    """Global error handler for all commands"""
    if isinstance(error, commands.CommandOnCooldown):
        embed = discord.Embed(
            title="⌛ Command on Cooldown",
            description=f"This command is on cooldown. Try again in {error.retry_after:.1f} seconds.",
            color=COLORS["yellow"]
        )
    elif isinstance(error, commands.MissingRequiredArgument):
        embed = discord.Embed(
            title="❓ Missing Argument",
            description=f"Missing required argument: {error.param}",
            color=COLORS["yellow"]
        )
    elif isinstance(error, commands.CommandNotFound):
        embed = discord.Embed(
            title="❓ Command Not Found",
            description="Command not found. Type `>help` to see available commands.",
            color=COLORS["yellow"]
        )
    else:
        logger.error(f"Unhandled command error: {str(error)}")
        embed = discord.Embed(
            title="❌ Error",
            description=f"An error occurred: {str(error)}",
            color=COLORS["red"]
        )
    
    await ctx.send(embed=embed)

@bot.command(name='status')
async def status(ctx):
    """Check the bot's status and API health"""
    try:
        # Test Gemini API
        model = genai.GenerativeModel('gemini-1.5-pro')
        response = model.generate_content("Hello")
        
        # Get conversation counts
        total_conversations = len(conversations)
        
        # Get user's conversation info
        user_id = str(ctx.author.id)
        conversation_key = f"user:{user_id}"
        has_conversation = conversation_key in conversations
        
        if has_conversation:
            created_at = conversations[conversation_key]["created_at"]
            expiry_date = created_at + timedelta(days=7)
            days_left = (expiry_date - datetime.now()).days
            expiry_info = f"Expires in {days_left} days"
        else:
            expiry_info = "No active conversation"
        
        # Create status embed
        embed = discord.Embed(
            title="🤖 911 Intel Status",
            description="Here's the current status of 911 Intel and its Gemini AI systems.",
            color=COLORS["teal"]
        )
        
        # Add system status fields
        embed.add_field(name="Bot Status", value="✅ Online", inline=True)
        embed.add_field(name="Gemini API", value="✅ Connected", inline=True)
        embed.add_field(name="Gemini Model", value="gemini-1.5-pro", inline=True)
        embed.add_field(name="Response Sample", value=response.text[:100] + "...", inline=False)
        
        # Add conversation stats
        embed.add_field(name="Active Conversations", value=str(total_conversations), inline=True)
        embed.add_field(name="Your Conversation", value=expiry_info, inline=True)
        
        # Add creator info
        embed.add_field(name="Bot Identity", value="911 Intel", inline=True)
        embed.add_field(name="Creator", value="kelpyshades", inline=True)
        
        # Add metadata
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        embed.timestamp = ctx.message.created_at
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Status check failed: {str(e)}")
        error_embed = discord.Embed(
            title="⚠️ Status Check Failed",
            description=f"Status check failed: {str(e)}",
            color=COLORS["red"]
        )
        await ctx.send(embed=error_embed)

@bot.command(name='search')
async def search(ctx, *, query):
    """Search the web using SerpAPI and enhance results with Gemini"""
    user_id = str(ctx.author.id)
    
    # Check rate limits
    is_user_limited = await user_limiter.add_call(user_id)
    is_global_limited = await global_limiter.add_call("global")
    
    if is_user_limited or is_global_limited:
        retry_after = max(
            await user_limiter.get_retry_after(user_id),
            await global_limiter.get_retry_after("global")
        )
        await ctx.send(f"Rate limit reached. Please try again in {retry_after:.1f} seconds.")
        return
    
    # Let users know we're processing their search
    progress_message = await ctx.send("🔎 Searching the web...")
    
    async with ctx.typing():
        try:
            # 1. Get search results from SerpAPI
            search_results = await perform_serpapi_search(query)
            
            if not search_results:
                await progress_message.edit(content="😕 Sorry, I couldn't find any relevant search results.")
                return
            
            # 2. Format search results for Gemini
            formatted_results = format_search_results_for_gemini(search_results)
            
            # 3. Generate response using Gemini
            prompt = f"""
            I searched the web for: "{query}"
            
            Here are the search results:
            {formatted_results}
            
            Please provide a comprehensive but concise summary based on these search results.
            Answer the query "{query}" using the information from these sources.
            Include the most relevant facts and cite your sources using [1], [2], etc. 
            Format your response in a way that's easy to read.
            """
            
            response = text_model.generate_content(prompt)
            
            # 4. Create a nice looking embed
            embed = discord.Embed(
                title=f"🔍 Search results for: {query}",
                description=response.text,
                color=COLORS["orange"]
            )
            
            # Add footer with sources
            sources_text = ""
            for i, result in enumerate(search_results[:5]):
                sources_text += f"[{i+1}] [{result['title']}]({result['link']})\n"
                if len(sources_text) > 900:  # Avoid Discord's field value limit
                    sources_text += "...(more results available)"
                    break
                    
            if sources_text:
                embed.add_field(name="📚 Sources", value=sources_text, inline=False)
            
            # Set thumbnail if available
            if search_results and "thumbnail" in search_results[0]:
                embed.set_thumbnail(url=search_results[0]["thumbnail"])
            
            # Add timestamp
            embed.timestamp = ctx.message.created_at
            
            # 5. Send the embed
            await progress_message.edit(content=None, embed=embed)
            
        except Exception as e:
            if "401" in str(e):
                error_message = "Search API authentication failed. Please check your SerpAPI key."
            else:
                error_message = f"Error performing search: {str(e)}"
            
            logger.error(error_message)
            error_embed = discord.Embed(
                title="❌ Search Error",
                description=error_message,
                color=COLORS["red"]
            )
            await progress_message.edit(content=None, embed=error_embed)

async def perform_serpapi_search(query):
    """Perform a search using SerpAPI"""
    SERPAPI_KEY = os.getenv('SERPAPI_KEY')
    
    if not SERPAPI_KEY:
        raise ValueError("Missing SerpAPI key in environment variables")
    
    encoded_query = quote_plus(query)
    url = f"https://serpapi.com/search.json?q={encoded_query}&api_key={SERPAPI_KEY}&num=8"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                error_text = await response.text()
                logger.error(f"SerpAPI error: {error_text}")
                raise Exception(f"Search API returned status {response.status}")
            
            data = await response.json()
            
            if "organic_results" not in data:
                return []
                
            results = []
            for item in data["organic_results"][:8]:  # Get top 8 results
                result = {
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "snippet": item.get("snippet", "No description available.")
                }
                
                # Add thumbnail if available
                if "thumbnail" in item:
                    result["thumbnail"] = item["thumbnail"]
                    
                results.append(result)
                
            return results

def format_search_results_for_gemini(results):
    """Format search results into a string for Gemini"""
    formatted = ""
    for i, result in enumerate(results):
        formatted += f"[{i+1}] {result['title']}\n"
        formatted += f"URL: {result['link']}\n"
        formatted += f"Snippet: {result['snippet']}\n\n"
    return formatted

# Run the bot with connection error handling
def run_bot():
    while True:
        try:
            bot.run(DISCORD_TOKEN)
        except discord.errors.LoginFailure:
            logger.error("Invalid Discord token. Please check your .env file.")
            break
        except Exception as e:
            logger.error(f"Connection error: {str(e)}")
            logger.info("Attempting to reconnect in 60 seconds...")
            time.sleep(60)

if __name__ == "__main__":
    run_bot()

class CustomHelpCommand(commands.DefaultHelpCommand):
    """Custom help command with embeds"""
    
    async def send_bot_help(self, mapping):
        embed = discord.Embed(
            title="🤖 911 Intel Commands",
            description="Here are all the commands you can use with 911 Intel, powered by Google Gemini's advanced AI capabilities:",
            color=COLORS["blue"]
        )
        
        for cog, commands in mapping.items():
            command_signatures = [self.get_command_signature(c) for c in commands]
            if command_signatures:
                cog_name = getattr(cog, "qualified_name", "No Category")
                embed.add_field(name=cog_name, value="\n".join(command_signatures), inline=False)
        
        embed.set_footer(text="911 Intel | Designed by kelpyshades | Powered by Google Gemini")
        
        channel = self.get_destination()
        await channel.send(embed=embed)
    
    async def send_command_help(self, command):
        embed = discord.Embed(
            title=f"Command: {command.name}",
            description=command.help or "No description available.",
            color=COLORS["blue"]
        )
        
        embed.add_field(name="Usage", value=self.get_command_signature(command), inline=False)
        
        channel = self.get_destination()
        await channel.send(embed=embed)

# Set up the custom help command
bot.help_command = CustomHelpCommand()

# Add a background task to check and reset old conversations
@tasks.loop(hours=24)  # Check once per day
async def check_conversation_age():
    """Check conversation age and reset those older than a week"""
    current_time = datetime.now()
    reset_count = 0
    
    # Find conversations older than a week
    for key, value in list(conversations.items()):
        if current_time - value["created_at"] > timedelta(days=7):
            del conversations[key]
            reset_count += 1
    
    if reset_count > 0:
        logger.info(f"Auto-reset {reset_count} conversations that were over a week old")

@bot.command(name='expiry')
async def check_expiry(ctx):
    """Check when your current conversation will expire"""
    user_id = str(ctx.author.id)
    conversation_key = f"user:{user_id}"
    
    if conversation_key not in conversations:
        embed = discord.Embed(
            title="No Active Conversation",
            description="You don't have an active conversation yet.",
            color=COLORS["yellow"]
        )
    else:
        created_at = conversations[conversation_key]["created_at"]
        expiry_date = created_at + timedelta(days=7)
        now = datetime.now()
        days_left = (expiry_date - now).days
        hours_left = int(((expiry_date - now).seconds) / 3600)
        
        embed = discord.Embed(
            title="Conversation Expiry",
            description=f"Your conversation will automatically reset in {days_left} days and {hours_left} hours.",
            color=COLORS["teal"]
        )
        embed.add_field(name="Created", value=created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
        embed.add_field(name="Expires", value=expiry_date.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    
    embed.set_footer(text=f"Requested by {ctx.author.display_name}")
    embed.timestamp = ctx.message.created_at
    await ctx.send(embed=embed)