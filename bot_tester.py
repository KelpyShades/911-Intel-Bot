import os
import time
import asyncio
import logging
import google.generativeai as genai
from collections import defaultdict
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot_test.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("gemini-bot-tester")

# Load environment variables
load_dotenv()
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# Configure the Gemini API
genai.configure(api_key=GEMINI_API_KEY)

# RateLimiter class for testing rate limiting
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

# Conversation storage (simulates what the bot would maintain)
conversations = {}

# Bot template for conversation starters
bot_template = [
    {'role': 'user', 'parts': ["Hi!"]},
    {'role': 'model', 'parts': ["Hello! I am 911 Intel powered by Google Gemini!"]},
    {'role': 'user', 'parts': ["Please give helpful and concise answers."]},
    {'role': 'model', 'parts': ["I'll do my best to be helpful and concise!"]},
]

# Get conversation for a specific channel and user
def get_conversation(channel_id, user_id):
    conversation_key = f"{channel_id}:{user_id}"
    
    if conversation_key not in conversations:
        try:
            model = genai.GenerativeModel('gemini-1.5-pro')
            conversations[conversation_key] = model.start_chat(history=bot_template)
            logger.info(f"Created new conversation for {conversation_key}")
        except Exception as e:
            logger.error(f"Error creating conversation: {str(e)}")
            raise
    
    return conversations[conversation_key]

# Get Gemini response with error handling
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

# Simulate ask command
async def simulate_ask_command(user_id, channel_id, question, user_limiter, global_limiter):
    # Check rate limits
    is_user_limited = await user_limiter.add_call(user_id)
    is_global_limited = await global_limiter.add_call("global")
    
    if is_user_limited:
        retry_after = await user_limiter.get_retry_after(user_id)
        print(f"⚠️ Rate limit: You're sending messages too quickly! Please wait {retry_after:.1f} seconds.")
        return
    
    if is_global_limited:
        retry_after = await global_limiter.get_retry_after("global")
        print(f"⚠️ Rate limit: The bot is handling too many requests. Please try again in {retry_after:.1f} seconds.")
        return
    
    print(f"💬 User asks: {question}")
    print("🤖 Bot is typing...")
    
    try:
        # Get the channel-specific conversation for this user
        conversation = get_conversation(channel_id, user_id)
        
        # Get response from Gemini
        response_text = await get_gemini_response(conversation, question)
        
        print(f"🤖 Bot responds: {response_text}")
            
    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")
        print(f"❌ Error: {str(e)}")

# Simulate forget command
async def simulate_forget_command(user_id, channel_id, target="user"):
    keys_to_remove = []
    conversation_key = f"{channel_id}:{user_id}"
    
    if target.lower() == "user":
        # Remove just this user in this channel
        keys_to_remove = [conversation_key]
    elif target.lower() == "channel":
        # Remove all conversations in this channel
        keys_to_remove = [key for key in conversations.keys() if key.startswith(f"{channel_id}:")]
    elif target.lower() == "all":
        # Remove all conversations for this user
        keys_to_remove = [key for key in conversations.keys() if key.endswith(f":{user_id}")]
    
    for key in keys_to_remove:
        if key in conversations:
            del conversations[key]
    
    if keys_to_remove:
        print(f"🧹 Cleared {len(keys_to_remove)} conversation(s)!")
    else:
        print("🔍 No conversations were found to clear.")

# Test rate limiting
async def test_rate_limiting():
    print("\n=== Testing Rate Limiting ===")
    user_limiter = RateLimiter(max_calls=3, time_frame=10)  # 3 calls per 10 seconds
    
    for i in range(5):
        is_limited = await user_limiter.add_call("test_user")
        print(f"Call {i+1}: {'⛔ Rate limited!' if is_limited else '✅ Allowed'}")
        if not is_limited and i < 4:  # Don't wait after the last call
            print("Waiting 1 second...")
            await asyncio.sleep(1)
    
    print("\nWaiting 10 seconds for cooldown...")
    await asyncio.sleep(10)
    
    is_limited = await user_limiter.add_call("test_user")
    print(f"After cooldown: {'⛔ Rate limited!' if is_limited else '✅ Allowed'}")

# Test error handling
async def test_error_handling():
    print("\n=== Testing Error Handling ===")
    try:
        # Test with invalid API key
        original_key = os.environ.get('GEMINI_API_KEY')
        os.environ['GEMINI_API_KEY'] = 'invalid_key'
        genai.configure(api_key='invalid_key')
        
        model = genai.GenerativeModel('gemini-pro')
        print("Testing with invalid API key...")
        try:
            response = model.generate_content("Hello")
            print(f"Response: {response.text}")
        except Exception as e:
            print(f"✅ Caught error as expected: {type(e).__name__}: {str(e)}")
        
        # Restore original key
        os.environ['GEMINI_API_KEY'] = original_key
        genai.configure(api_key=original_key)
        
        # Test with invalid model
        try:
            model = genai.GenerativeModel('non-existent-model')
            response = model.generate_content("Hello")
            print(f"Response: {response.text}")
        except Exception as e:
            print(f"✅ Caught error as expected: {type(e).__name__}: {str(e)}")
            
    except Exception as e:
        print(f"❌ Unexpected error in error handling test: {str(e)}")
    finally:
        # Ensure we restore the original key
        os.environ['GEMINI_API_KEY'] = original_key
        genai.configure(api_key=original_key)

# Interactive terminal chat
async def terminal_chat():
    print("\n=== Discord Bot Terminal Tester ===")
    print("This simulates interaction with your Discord bot.")
    print("Available commands:")
    print("  !ask [question] - Ask the bot a question")
    print("  !forget [user|channel|all] - Clear conversation history")
    print("  !test rate - Test rate limiting")
    print("  !test errors - Test error handling")
    print("  !test multimodal - Test multimodal capabilities")
    print("  !exit - Exit the terminal")
    
    # Set up rate limiters
    user_limiter = RateLimiter(max_calls=5, time_frame=60)  # 5 calls per minute per user
    global_limiter = RateLimiter(max_calls=30, time_frame=60)  # 30 calls per minute globally
    
    # Simulate a user and channel ID
    user_id = "test_user_123"
    channel_id = "test_channel_456"
    
    while True:
        user_input = input("\n> ")
        
        if user_input.lower() == "!exit":
            break
            
        elif user_input.lower().startswith("!ask "):
            question = user_input[5:].strip()
            await simulate_ask_command(user_id, channel_id, question, user_limiter, global_limiter)
            
        elif user_input.lower().startswith("!forget"):
            parts = user_input.split(maxsplit=1)
            target = parts[1] if len(parts) > 1 else "user"
            await simulate_forget_command(user_id, channel_id, target)
            
        elif user_input.lower() == "!test rate":
            await test_rate_limiting()
            
        elif user_input.lower() == "!test errors":
            await test_error_handling()
            
        elif user_input.lower() == "!test multimodal":
            await test_multimodal()
            
        else:
            print("Unknown command. Type !ask followed by your question.")

# Main function
async def main():
    print("Discord Gemini Bot Terminal Test")
    print("================================")
    
    # Check if Gemini API key is set
    if not GEMINI_API_KEY:
        print("❌ GEMINI_API_KEY not found in environment variables.")
        print("Please create a .env file with your GEMINI_API_KEY.")
        return
    
    # Test the Gemini API connection
    try:
        model = genai.GenerativeModel('gemini-1.5-pro')
        response = model.generate_content("Hello, are you working?")
        print("✅ Gemini API connection successful!")
        print(f"Gemini says: {response.text}\n")
    except Exception as e:
        print(f"❌ Gemini API connection failed: {str(e)}")
        return
    
    # Start interactive terminal chat
    await terminal_chat()

async def test_multimodal():
    print("\n=== Testing Multimodal Capabilities ===")
    # Initialize vision model
    vision_model = genai.GenerativeModel('gemini-1.5-flash')
    
    # Test with a local image if available
    try:
        image_path = input("Enter path to a test image (or press Enter to skip): ")
        if image_path and os.path.exists(image_path):
            with open(image_path, 'rb') as f:
                image_data = f.read()
            
            print("Analyzing image...")
            response = vision_model.generate_content([
                "Describe what you see in this image in detail.",
                image_data
            ])
            print(f"Response: {response.text}")
        else:
            print("Skipping image test.")
    except Exception as e:
        print(f"Error with image processing: {str(e)}")
    
    # Add similar tests for video and audio if needed

if __name__ == "__main__":
    asyncio.run(main())
