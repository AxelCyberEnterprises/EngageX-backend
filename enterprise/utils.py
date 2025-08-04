from django.conf import settings

def get_voice_for_question(rookie_type, sport_type=None):
    """
    Determine the appropriate TTS voice based on rookie type and sport type.
    
    Args:
        rookie_type (str): The type of rookie ('gm', 'coach', etc.)
        sport_type (str, optional): The sport type ('nba', 'nfl', 'wnba', etc.)
        
    Returns:
        str: The voice ID to use for TTS
    """
    # Default voice (from settings or fallback to 'echo')
    default_voice = getattr(settings, 'TTS_VOICE', 'echo')
    
    # If no rookie type is provided, return the default voice
    if not rookie_type:
        return default_voice
        
    # Convert to lowercase for case-insensitive comparison
    rookie_type = rookie_type.lower()
    
    # GM rookie type always uses female voice
    if rookie_type == 'gm':
        return 'nova'  # Female voice
        
    # For coach rookie type, check the sport type
    if rookie_type == 'coach' and sport_type:
        sport_type = sport_type.lower()
        # Male voice for NBA and NFL
        if sport_type in ['nba', 'nfl']:
            return 'onyx'  # Male voice
        # Female voice for WNBA
        elif sport_type == 'wnba':
            return 'coral'  # Female voice
    
    # Default case - return the default voice from settings
    return default_voice
