from datetime import datetime
from typing import Dict
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SocialMediaService:
    """Handle social media posting for escalated issues"""
    
    def __init__(self, twitter_api_key=None, instagram_token=None):
        self.twitter_api_key = twitter_api_key
        self.instagram_token = instagram_token
    
    def post_to_twitter(self, issue: Dict) -> Dict:
        """Post issue to Twitter"""
        try:
            tweet_text = f"""
🚨 URGENT: {issue['title']}
📍 Location: {issue['geotag_address']}
⚠️ Severity: {issue['severity']}/10
🕐 Reported: {issue['created_at']}
#UrbanManagement #CityServices #NeedAction
            """
            
            # In production, use Tweepy
            # client = tweepy.Client(bearer_token=self.twitter_api_key)
            # response = client.create_tweet(text=tweet_text)
            
            logger.info("Tweet posted (simulated)")
            return {
                'platform': 'twitter',
                'posted': True,
                'post_text': tweet_text,
                'timestamp': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error posting to Twitter: {str(e)}")
            return {'platform': 'twitter', 'posted': False, 'error': str(e)}
    
    def post_to_instagram(self, issue: Dict) -> Dict:
        """Post issue to Instagram"""
        try:
            caption = f"""
🚨 Attention Citizens!
{issue['title']}

📍 {issue['geotag_address']}
⚠️ Severity Level: {issue['severity']}/10

We need immediate action on this issue!
Report date: {issue['created_at']}

#UrbanManagement #CityServices
            """
            
            logger.info("Instagram post created (simulated)")
            return {
                'platform': 'instagram',
                'posted': True,
                'caption': caption,
                'timestamp': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error posting to Instagram: {str(e)}")
            return {'platform': 'instagram', 'posted': False, 'error': str(e)}
    
    def post_completion_update(self, issue: Dict) -> Dict:
        """Post completion update with before/after photos"""
        try:
            completion_text = f"""
✅ ISSUE RESOLVED!

📌 {issue['title']}
📍 {issue['geotag_address']}

Before: [Image]
After: [Image]

Completion Date: {issue['completed_at']}
Thank you for reporting!

#UrbanManagement #ServiceExcellence
            """
            
            logger.info("Completion post created (simulated)")
            return {
                'platform': 'multi',
                'posted': True,
                'message': completion_text,
                'timestamp': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error posting completion: {str(e)}")
            return {'posted': False, 'error': str(e)}