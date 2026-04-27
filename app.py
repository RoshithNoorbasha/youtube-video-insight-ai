import streamlit as st
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
    InvalidVideoId
)
import google.generativeai as genai
import os
from fpdf import FPDF
from dotenv import load_dotenv
import json
from urllib.parse import urlparse, parse_qs
from streamlit_option_menu import option_menu
import re
import tempfile
import yt_dlp
import streamlit.components.v1 as components
import time
from datetime import datetime
import io

load_dotenv()

# Check for API key
if not os.getenv("GEMINI_API"):
    st.error("GEMINI_API key not found in environment variables. Please check your .env file.")
    st.stop()

genai.configure(api_key=os.getenv("GEMINI_API"))

content_model = genai.GenerativeModel('gemini-2.0-flash',
    system_instruction="You are an expert content analyzer and learning path creator."
)

vision_model = genai.GenerativeModel('gemini-2.0-flash-exp', 
    system_instruction="You are a very good video analyzer and information extractor from video"
)

def extract_video_id(url):
    """Extract YouTube video ID from various URL formats"""
    if not url:
        return None
    
    parsed = urlparse(url)
    if parsed.hostname == 'youtu.be':
        return parsed.path[1:]
    if parsed.hostname in ('www.youtube.com', 'youtube.com'):
        if parsed.path == '/watch':
            return parse_qs(parsed.query).get('v', [None])[0]
        if parsed.path.startswith('/embed/'):
            return parsed.path.split('/')[2]
        if parsed.path.startswith('/v/'):
            return parsed.path.split('/')[2]
    return None

def get_video_info(video_id):
    """Fetch video metadata using yt-dlp"""
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            
            tags = info.get('tags', [])
            categories = info.get('categories', [])
            
            return {
                'title': info.get('title', 'Unknown'),
                'duration': info.get('duration', 0),
                'view_count': info.get('view_count', 0),
                'like_count': info.get('like_count', 0),
                'upload_date': info.get('upload_date', 'Unknown'),
                'channel': info.get('channel', 'Unknown'),
                'description': info.get('description', 'No description available')[:1000],
                'tags': tags[:10] if tags else [],
                'categories': categories
            }
    except Exception as e:
        st.warning(f"Could not fetch full video info: {str(e)}")
        return {
            'title': 'Unknown',
            'duration': 0,
            'view_count': 0,
            'like_count': 0,
            'upload_date': 'Unknown',
            'channel': 'Unknown',
            'description': 'No description available',
            'tags': [],
            'categories': []
        }

def transcribe_video(video_id):
    """Transcribe video with comprehensive error handling"""
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        
        # Try to get English transcript first
        try:
            transcript = transcript_list.find_transcript(['en'])
            transcript_data = transcript.fetch()
            return {
                'translated': ' '.join([i['text'] for i in transcript_data]),
                'original': ' '.join([i['text'] for i in transcript_data]),
                'language': 'en',
                'is_translated': False
            }
        except NoTranscriptFound:
            # If no English transcript, get available one and translate
            for transcript_obj in transcript_list:
                try:
                    original = transcript_obj.fetch()
                    original_lang = transcript_obj.language_code
                    
                    if original_lang != 'en':
                        try:
                            translated = transcript_obj.translate('en').fetch()
                            return {
                                'translated': ' '.join([i['text'] for i in translated]),
                                'original': ' '.join([i['text'] for i in original]),
                                'language': original_lang,
                                'is_translated': True
                            }
                        except:
                            return {
                                'translated': ' '.join([i['text'] for i in original]),
                                'original': ' '.join([i['text'] for i in original]),
                                'language': original_lang,
                                'is_translated': False
                            }
                except:
                    continue
            return None
            
    except Exception as e:
        st.info(f"Transcript not available: {str(e)}")
        return None

def analyze_with_vision(my_file):
    """Analyze video content using Gemini vision capabilities"""
    try:
        response = vision_model.generate_content([ 
            """Analyze this video content for entertainment purposes. Return STRICT JSON format with: 
            {
                "genre": string, 
                "mood": string, 
                "similar_content_suggestions": array of strings, 
                "key_elements": array of strings, 
                "audience_options": array of strings,
                "visual_quality": string,
                "production_style": string
            }""",
            my_file
        ])
        return parse_gemini_response(response.text)
    except Exception as e:
        st.error(f"Vision analysis failed: {str(e)}")
        return None

def wait_for_file_active(file, max_retries=5, delay=5):
    """Wait for uploaded file to become active"""
    retries = 0
    while retries < max_retries:
        try:
            file_state = genai.get_file(file.name)
            if file_state.state.name == "ACTIVE":
                return True
            elif file_state.state.name == "FAILED":
                st.error("File upload failed!")
                return False
        except:
            pass
        time.sleep(delay)
        retries += 1
    st.error("File activation timed out!")
    return False

DOWNLOAD_DIR = os.path.abspath("downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def dwl_vid(video_url):
    """Download video with progress tracking"""
    try:
        ydl_opts = {
            'format': 'best[ext=mp4]/best',
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(video_url, download=True)
            if 'entries' in info_dict:
                info_dict = info_dict['entries'][0]
            file_path = ydl.prepare_filename(info_dict)
            
            # Find the actual downloaded file
            if not os.path.exists(file_path):
                base = os.path.splitext(file_path)[0]
                for ext in ['.mp4', '.webm', '.mkv']:
                    test_path = base + ext
                    if os.path.exists(test_path):
                        return test_path
            return file_path if os.path.exists(file_path) else None
    except Exception as e:
        st.error(f"Download failed: {str(e)}")
        return None

def parse_gemini_response(response_text):
    """Parse JSON response from Gemini"""
    try:
        cleaned = re.sub(r'```json\s*|\s*```', '', response_text, flags=re.IGNORECASE)
        cleaned = re.sub(r'```\s*|\s*```', '', cleaned)
        json_match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return None
    except Exception as e:
        st.error(f"Failed to parse response: {str(e)}")
        return None

def generate_roadmap_without_transcript(video_info, audience, roadmap_type="Comprehensive"):
    """Generate a learning roadmap when no transcript is available"""
    prompt = f"""
    Create a detailed learning roadmap based on the following video information:
    
    VIDEO METADATA:
    Title: {video_info.get('title', 'Unknown')}
    Channel: {video_info.get('channel', 'Unknown')}
    Description: {video_info.get('description', 'No description available')}
    Tags: {', '.join(video_info.get('tags', []))}
    Categories: {', '.join(video_info.get('categories', []))}
    
    Target Audience: {audience}
    Roadmap Type: {roadmap_type}
    
    Based on this information, create a comprehensive learning roadmap that includes:
    
    1. **Prerequisites** - What learners should know before starting
    2. **Learning Milestones** - Key stages in the learning journey (at least 5)
    3. **Estimated Timeline** - Suggested time for each milestone
    4. **Resources Needed** - Tools, software, or materials mentioned
    5. **Practical Exercises** - Hands-on activities to reinforce learning
    6. **Assessment Criteria** - How to know when each milestone is achieved
    7. **Next Steps** - Advanced topics to explore after completion
    
    Format the roadmap with clear headings and bullet points. Make it actionable and practical.
    Even without the full transcript, use the title, channel context, and description to infer the content.
    """
    
    response = content_model.generate_content(prompt)
    return response.text

def generate_summary_without_transcript(video_info, analysis_type, audience):
    """Generate a summary when no transcript is available"""
    prompt = f"""
    Create a comprehensive summary and analysis based on the following video information:
    
    VIDEO METADATA:
    Title: {video_info.get('title', 'Unknown')}
    Channel: {video_info.get('channel', 'Unknown')}
    Description: {video_info.get('description', 'No description available')}
    Tags: {', '.join(video_info.get('tags', []))}
    Duration: {video_info.get('duration', 0)} seconds
    
    Analysis Type: {analysis_type}
    Target Audience: {audience}
    
    Based on this information, provide:
    
    1. **Video Overview** - What this video is likely about
    2. **Key Topics Covered** - Main subjects based on title and description
    3. **Target Audience Fit** - How well this suits the specified audience
    4. **Value Assessment** - What viewers can expect to learn or gain
    5. **Content Quality Indicators** - Based on channel, views, and engagement
    6. **Suggested Prerequisites** - What viewers should know beforehand
    7. **Related Topics** - Subjects that complement this content
    
    Be honest about the limitations of analyzing without transcript, but provide the best possible insights based on available metadata.
    """
    
    response = content_model.generate_content(prompt)
    return response.text

def generate_pdf_roadmap(roadmap_content, title):
    """Generate PDF from roadmap content"""
    try:
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", size=12)
        
        # Add title
        pdf.set_font("Arial", 'B', 16)
        # Clean title for PDF
        clean_title = title.encode('latin-1', 'replace').decode('latin-1')
        pdf.cell(0, 10, f"Learning Roadmap: {clean_title}", ln=True)
        pdf.ln(10)
        
        # Add content
        pdf.set_font("Arial", size=12)
        
        # Split content into lines and add to PDF
        lines = roadmap_content.split('\n')
        for line in lines:
            if line.strip():
                # Clean line for PDF
                clean_line = line.encode('latin-1', 'replace').decode('latin-1')
                # Check if it's a heading (starts with ** or has specific patterns)
                if line.startswith('**') or line.startswith('#') or line.startswith('1.') or line.startswith('2.') or line.startswith('3.'):
                    pdf.set_font("Arial", 'B', 12)
                    pdf.multi_cell(0, 8, clean_line)
                    pdf.set_font("Arial", size=12)
                else:
                    pdf.multi_cell(0, 6, clean_line)
                pdf.ln(2)
        
        # Return PDF as bytes
        pdf_output = pdf.output(dest='S')
        return pdf_output.encode('latin-1') if isinstance(pdf_output, str) else pdf_output
        
    except Exception as e:
        st.warning(f"PDF generation failed: {str(e)}")
        # Return plain text as fallback
        return roadmap_content.encode('utf-8')

# Page configuration
st.set_page_config(page_title="YouTube Video Insight AI", layout="wide", page_icon="🎬")

# Initialize session state
if 'current_video' not in st.session_state:
    st.session_state.current_video = {'id': None, 'type': None}
if 'analysis' not in st.session_state:
    st.session_state.analysis = None
if 'transcript' not in st.session_state:
    st.session_state.transcript = None
if 'chat_history' not in st.session_state:
    st.session_state.chat_history = {}
if 'audience' not in st.session_state:
    st.session_state.audience = "General"
if 'processing' not in st.session_state:
    st.session_state.processing = False
if 'video_info' not in st.session_state:
    st.session_state.video_info = None
if 'summary_generated' not in st.session_state:
    st.session_state.summary_generated = False
if 'last_summary' not in st.session_state:
    st.session_state.last_summary = None
if 'roadmap_generated' not in st.session_state:
    st.session_state.roadmap_generated = False
if 'last_roadmap' not in st.session_state:
    st.session_state.last_roadmap = None

# Main title
st.title("🎬 YouTube Video Insight AI")
st.markdown("*Your AI-powered YouTube video analysis companion*")

with st.sidebar:
    st.header("📺 Video Input")
    video_url = st.text_input("Enter YouTube URL:", placeholder="https://www.youtube.com/watch?v=...")
    video_id = extract_video_id(video_url) if video_url else None
    
    if video_id:
        # Get video info
        if st.session_state.video_info is None or st.session_state.current_video['id'] != video_id:
            with st.spinner("Fetching video info..."):
                st.session_state.video_info = get_video_info(video_id)
        
        # Display video metrics
        if st.session_state.video_info:
            st.markdown("### 📊 Video Info")
            st.markdown(f"**Title:** {st.session_state.video_info['title'][:60]}...")
            st.markdown(f"**Channel:** {st.session_state.video_info['channel']}")
            col1, col2 = st.columns(2)
            with col1:
                views = st.session_state.video_info['view_count']
                st.metric("👁️ Views", f"{views:,}" if views else "N/A")
            with col2:
                duration = st.session_state.video_info['duration']
                if duration:
                    minutes = duration // 60
                    seconds = duration % 60
                    st.metric("⏱️ Duration", f"{minutes}:{seconds:02d}")
                else:
                    st.metric("⏱️ Duration", "N/A")
        
        analysis_type = st.radio(
            "🔍 Select Analysis Type:", 
            ("📚 Knowledge Analytics", "🎬 Entertainment Analytics"),
            help="Knowledge: Educational/content analysis | Entertainment: Visual/style analysis"
        )
        
        if "Knowledge" in analysis_type:
            st.info("📚 Knowledge mode analyzes educational and informative content")
        else:
            st.info("🎬 Entertainment mode analyzes visual and stylistic elements")
        
        # Reset if new video
        if (st.session_state.current_video['id'] != video_id or 
            st.session_state.current_video['type'] != analysis_type):
            st.session_state.chat_history = {}
            st.session_state.current_video = {'id': video_id, 'type': analysis_type}
            st.session_state.analysis = None
            st.session_state.transcript = None
            st.session_state.summary_generated = False
            st.session_state.roadmap_generated = False
            st.session_state.last_summary = None
            st.session_state.last_roadmap = None
        
        # Video preview
        st.markdown("### 🎥 Preview")
        components.html(
            f"""<iframe width="100%" height="200" 
            src="https://www.youtube.com/embed/{video_id}" 
            frameborder="0" allowfullscreen></iframe>""",
            height=220
        )
        
        # Process button
        if st.button("🚀 Process Video", disabled=st.session_state.processing, use_container_width=True):
            st.session_state.processing = True
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            try:
                if "Knowledge" in analysis_type:
                    status_text.text("📝 Fetching transcript...")
                    progress_bar.progress(30)
                    st.session_state.transcript = transcribe_video(video_id)
                    
                    if st.session_state.transcript:
                        status_text.text("🧠 Analyzing content...")
                        progress_bar.progress(60)
                        prompt = f"""Analyze this transcript for knowledge content. Return JSON with:
                        {{
                            "video_type": string,
                            "custom_prompt": string,
                            "audience_options": array of strings,
                            "key_topics": array of strings,
                            "difficulty_level": string
                        }}
                        Transcript (first 10000 chars): {st.session_state.transcript['translated'][:10000]}
                        """
                        response = content_model.generate_content(prompt)
                        st.session_state.analysis = parse_gemini_response(response.text)
                        progress_bar.progress(100)
                        status_text.text("✅ Analysis complete!")
                        st.success("✅ Video processed successfully with transcript!")
                    else:
                        status_text.text("⚠️ No transcript - using metadata analysis")
                        progress_bar.progress(100)
                        # Create analysis based on metadata
                        st.session_state.analysis = {
                            "video_type": "Content Analysis (Metadata-based)",
                            "custom_prompt": "Analyze based on video metadata",
                            "audience_options": ["General", "Beginners", "Intermediate", "Advanced"],
                            "key_topics": ["Based on video title and description"],
                            "difficulty_level": "Varies",
                            "note": "Analysis based on video metadata as transcript unavailable"
                        }
                        st.info("ℹ️ No transcript available. Analysis will be based on video title, description, and channel information.")
                
                else:  # Entertainment Analytics
                    status_text.text("📝 Checking for transcript...")
                    progress_bar.progress(20)
                    st.session_state.transcript = transcribe_video(video_id)
                    
                    if st.session_state.transcript:
                        st.success("✓ Transcript available for enhanced analysis")
                    else:
                        st.info("ℹ️ No transcript - will analyze based on video content and metadata")
                    
                    status_text.text("📥 Downloading video...")
                    progress_bar.progress(40)
                    video_path = dwl_vid(video_url)
                    
                    if video_path and os.path.exists(video_path):
                        status_text.text("☁️ Uploading to AI...")
                        progress_bar.progress(60)
                        my_file = genai.upload_file(video_path)
                        
                        if wait_for_file_active(my_file):
                            status_text.text("🎨 Analyzing video content...")
                            progress_bar.progress(80)
                            vision_analysis = analyze_with_vision(my_file)
                            
                            # Combine vision analysis with metadata
                            st.session_state.analysis = vision_analysis if vision_analysis else {
                                "genre": "Not specified",
                                "mood": "Not specified",
                                "similar_content_suggestions": [],
                                "key_elements": [],
                                "audience_options": ["General", "Fans", "Casual Viewers"],
                                "visual_quality": "Based on available data",
                                "production_style": "Standard"
                            }
                            progress_bar.progress(100)
                            status_text.text("✅ Analysis complete!")
                            st.success("✅ Video processed successfully with visual analysis!")
                        else:
                            st.error("Failed to upload video")
                            st.session_state.analysis = None
                        
                        # Cleanup
                        try:
                            if os.path.exists(video_path):
                                os.remove(video_path)
                        except:
                            pass
                    else:
                        st.error("Failed to download video")
                        st.session_state.analysis = None
                        
            except Exception as e:
                st.error(f"Processing error: {str(e)}")
                st.session_state.analysis = None
            finally:
                st.session_state.processing = False
                time.sleep(2)
                progress_bar.empty()
                status_text.empty()
                st.rerun()
    else:
        if video_url:
            st.error("Invalid YouTube URL. Please check and try again.")
        else:
            st.info("Enter a YouTube URL to begin")

# Main content area
if st.session_state.current_video['id'] and st.session_state.video_info:
    if not st.session_state.analysis:
        st.info("👈 Click 'Process Video' in the sidebar to start analysis")
        
        # Show helpful tips
        with st.expander("💡 Features available even without transcript"):
            st.markdown("""
            **Knowledge Analytics Mode (Educational Content):**
            - ✅ Generate learning roadmaps from video metadata
            - ✅ Create comprehensive summaries
            - ✅ Interactive Q&A about the content
            - ✅ Audience-specific insights
            
            **Entertainment Analytics Mode:**
            - ✅ Visual and stylistic analysis
            - ✅ Genre and mood detection
            - ✅ Content recommendations
            - ✅ Production quality assessment
            """)
    else:
        # Audience selection
        if 'audience_options' in st.session_state.analysis:
            audience_options = st.session_state.analysis.get('audience_options', ['General', 'Beginners', 'Intermediate', 'Advanced'])
            if audience_options:
                st.session_state.audience = st.selectbox(
                    "👥 Select Target Audience:",
                    options=audience_options,
                    key='audience_selector'
                )
        
        # Menu options
        if "Knowledge" in st.session_state.current_video['type']:
            menu_options = ["📝 Summary", "🗺️ Roadmap", "📄 Transcript", "💬 Chat"]
            menu_icons = ["file-text", "map", "card-text", "chat"]
        else:
            menu_options = ["📝 Summary", "🎬 Recommendations", "📄 Transcript", "💬 Chat"]
            menu_icons = ["file-text", "film", "card-text", "chat"]
        
        selected = option_menu(
            menu_title=None,
            options=menu_options,
            icons=menu_icons,
            default_index=0,
            orientation="horizontal",
            styles={
                "container": {"padding": "0!important", "margin-bottom": "20px"},
                "icon": {"font-size": "20px"},
                "nav-link": {"font-size": "16px", "text-align": "center"},
                "nav-link-selected": {"background-color": "#4CAF50", "color": "white"},
            }
        )
        
        # Summary Section
        if selected == "📝 Summary":
            st.header("📝 Video Summary & Analysis")
            
            col1, col2 = st.columns([2, 1])
            with col1:
                generate_btn = st.button("✨ Generate Summary", type="primary", use_container_width=True)
            with col2:
                audience_level = st.selectbox("Detail Level:", ["Quick", "Standard", "Comprehensive"], key="summary_level")
            
            if generate_btn or st.session_state.summary_generated:
                with st.spinner(f"Generating {audience_level} summary..."):
                    try:
                        # Check if transcript is available
                        if st.session_state.transcript and st.session_state.transcript.get('translated'):
                            # Use transcript-based summary
                            if "Knowledge" in st.session_state.current_video['type']:
                                prompt = st.session_state.analysis.get('custom_prompt', 
                                    f"Provide detailed summary with respect to {st.session_state.audience} audience")
                                content = st.session_state.transcript['translated'][:15000]
                            else:
                                prompt = "Provide comprehensive entertainment analysis summary"
                                content = json.dumps(st.session_state.analysis)
                            
                            response = content_model.generate_content(
                                f"Prompt: {prompt}\n\n"
                                f"Target Audience: {st.session_state.audience}\n"
                                f"Detail Level: {audience_level}\n\n"
                                f"Content: {content}\n\n"
                                f"Provide a well-structured, {audience_level.lower()} summary."
                            )
                            summary = response.text
                        else:
                            # Use metadata-based summary
                            summary = generate_summary_without_transcript(
                                st.session_state.video_info,
                                st.session_state.current_video['type'],
                                st.session_state.audience
                            )
                        
                        st.session_state.last_summary = summary
                        st.session_state.summary_generated = True
                        
                        # Display summary
                        st.markdown("### 💡 Key Insights")
                        st.markdown(summary)
                        
                        # Download button
                        st.download_button(
                            label="📥 Download Summary",
                            data=summary,
                            file_name=f"summary_{st.session_state.current_video['id']}.txt",
                            mime="text/plain"
                        )
                    except Exception as e:
                        st.error(f"Failed to generate summary: {str(e)}")
        
        # Roadmap Section (Knowledge only)
        elif selected == "🗺️ Roadmap" and "Knowledge" in st.session_state.current_video['type']:
            st.header("🗺️ Learning Roadmap Generator")
            
            st.markdown("""
            Create a structured learning path based on this video content. 
            Even without a transcript, the AI will analyze the video metadata to create a meaningful roadmap.
            """)
            
            col1, col2 = st.columns(2)
            with col1:
                roadmap_type = st.selectbox(
                    "Roadmap Style:",
                    ["Comprehensive", "Quick Start", "Deep Dive", "Project-Based", "Theory-Focused"],
                    key="roadmap_style"
                )
            with col2:
                timeline = st.selectbox(
                    "Timeline Preference:",
                    ["Flexible", "1 Week", "2 Weeks", "1 Month", "3 Months"],
                    key="roadmap_timeline"
                )
            
            if st.button("🛣️ Generate Learning Roadmap", type="primary", use_container_width=True):
                with st.spinner(f"Creating {roadmap_type} learning roadmap..."):
                    try:
                        if st.session_state.transcript and st.session_state.transcript.get('translated'):
                            # Use transcript-based roadmap
                            response = content_model.generate_content(
                                f"Create a {roadmap_type} learning roadmap for {st.session_state.audience} audience\n\n"
                                f"Timeline preference: {timeline}\n\n"
                                f"Based on the transcript: {st.session_state.transcript['translated'][:10000]}\n\n"
                                f"Include: prerequisites, milestones, estimated time, resources, practical exercises, and assessment criteria.\n"
                                f"Format with clear headings and bullet points."
                            )
                            roadmap = response.text
                        else:
                            # Use metadata-based roadmap
                            roadmap = generate_roadmap_without_transcript(
                                st.session_state.video_info,
                                st.session_state.audience,
                                roadmap_type
                            )
                            
                            # Add timeline note
                            roadmap += f"\n\n**Timeline Note:** This roadmap is designed for {timeline} completion but can be adjusted based on your pace."
                        
                        st.session_state.last_roadmap = roadmap
                        st.session_state.roadmap_generated = True
                        
                        # Display roadmap
                        st.markdown("### 📍 Your Learning Path")
                        st.markdown(roadmap)
                        
                        # Download buttons
                        col1, col2 = st.columns(2)
                        with col1:
                            st.download_button(
                                label="📥 Download Roadmap (TXT)",
                                data=roadmap,
                                file_name=f"roadmap_{st.session_state.current_video['id']}.txt",
                                mime="text/plain"
                            )
                        with col2:
                            # Create PDF version
                            pdf_data = generate_pdf_roadmap(roadmap, st.session_state.video_info['title'])
                            st.download_button(
                                label="📄 Download Roadmap (PDF)",
                                data=pdf_data,
                                file_name=f"roadmap_{st.session_state.current_video['id']}.pdf",
                                mime="application/pdf"
                            )
                    except Exception as e:
                        st.error(f"Failed to generate roadmap: {str(e)}")
                        st.info("Please try again or select a different roadmap style")
        
        # Recommendations Section (Entertainment only)
        elif selected == "🎬 Recommendations" and "Entertainment" in st.session_state.current_video['type']:
            st.header("🎬 Content Recommendations & Analysis")
            
            if st.session_state.analysis:
                # Display visual analysis metrics
                st.markdown("### 🎨 Visual & Stylistic Analysis")
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.markdown("**🎭 Genre**")
                    st.info(st.session_state.analysis.get('genre', 'Not specified'))
                with col2:
                    st.markdown("**🎨 Mood**")
                    st.info(st.session_state.analysis.get('mood', 'Not specified'))
                with col3:
                    st.markdown("**🎬 Production Style**")
                    st.info(st.session_state.analysis.get('production_style', 'Standard'))
                
                st.markdown("### ⭐ Key Visual Elements")
                key_elements = st.session_state.analysis.get('key_elements', [])
                if key_elements:
                    for element in key_elements:
                        st.markdown(f"• {element}")
                else:
                    st.markdown("No specific visual elements identified")
                
                st.markdown("### 📺 Similar Content Suggestions")
                recommendations = st.session_state.analysis.get('similar_content_suggestions', [])
                if recommendations:
                    for i, item in enumerate(recommendations, 1):
                        with st.expander(f"{i}. {item}"):
                            st.write(f"Recommended based on {st.session_state.analysis.get('genre', 'similar')} genre and visual style")
                else:
                    st.info("No specific recommendations available - try processing with transcript for better results")
            else:
                st.info("Process the video first to get recommendations")
        
        # Transcript Section - FIXED
        elif selected == "📄 Transcript":
            st.header("📄 Video Transcript")
            
            # Check if transcript exists and has content
            has_transcript = st.session_state.transcript and st.session_state.transcript.get('translated') and len(st.session_state.transcript['translated'].strip()) > 0
            
            if has_transcript:
                # Search functionality
                search_term = st.text_input("🔍 Search in transcript:", placeholder="Enter keywords...")
                
                tab1, tab2 = st.tabs(["📖 English Translation", f"📜 Original ({st.session_state.transcript.get('language', 'unknown')})"])
                
                with tab1:
                    transcript_text = st.session_state.transcript['translated']
                    if search_term:
                        # Highlight and show count
                        highlighted = transcript_text.replace(search_term, f"**{search_term}**")
                        st.markdown(highlighted)
                        count = transcript_text.lower().count(search_term.lower())
                        st.info(f"Found {count} occurrence(s) of '{search_term}'")
                    else:
                        st.text_area("Full Transcript", transcript_text, height=400, key="transcript_area")
                
                with tab2:
                    if st.session_state.transcript.get('original') and st.session_state.transcript['original'] != st.session_state.transcript['translated']:
                        st.text_area("Original Language Transcript", st.session_state.transcript['original'], height=400, key="original_transcript_area")
                    else:
                        st.info("Same as English translation")
                
                # Export options
                col1, col2 = st.columns(2)
                with col1:
                    st.download_button(
                        label="💾 Download as TXT",
                        data=st.session_state.transcript['translated'],
                        file_name=f"transcript_{st.session_state.current_video['id']}.txt",
                        mime="text/plain"
                    )
                with col2:
                    transcript_json = json.dumps(st.session_state.transcript, indent=2)
                    st.download_button(
                        label="📊 Download as JSON",
                        data=transcript_json,
                        file_name=f"transcript_{st.session_state.current_video['id']}.json",
                        mime="application/json"
                    )
            else:
                # No transcript available - show helpful message
                st.warning("⚠️ No transcript available for this video.")
                
                # Create expandable sections with helpful information
                with st.expander("📖 Why is there no transcript?"):
                    st.markdown("""
                    **Common reasons:**
                    - Video has no captions/subtitles enabled
                    - Auto-generated captions are disabled by the creator
                    - Video is very short (under 30 seconds)
                    - Video has no speech content (music only)
                    - Video language not supported for auto-captioning
                    """)
                
                with st.expander("💡 What can I do instead?"):
                    st.markdown("""
                    **You can still use these features:**
                    - ✅ **Generate summaries** using video metadata
                    - ✅ **Create learning roadmaps** without transcript  
                    - ✅ **Chat about the video** using available info
                    - ✅ **Get recommendations** (Entertainment mode)
                    
                    **Tips for finding videos with transcripts:**
                    - Look for the **CC icon** on YouTube
                    - Try educational channels (TED, Khan Academy, MIT)
                    - Search for "closed captions" or "subtitles"
                    - Use Entertainment mode for music/gaming content
                    """)
                
                with st.expander("🔄 How to get transcripts?"):
                    st.markdown("""
                    **For your own videos:**
                    1. Go to YouTube Studio
                    2. Select your video
                    3. Click on "Subtitles"
                    4. Add or generate captions
                    
                    **For better results:**
                    - Upload videos with clear speech
                    - Use videos from authoritative educational sources
                    - Wait for auto-captions to generate (takes time)
                    """)
                
                # Offer alternative action
                if st.button("🔄 Try Entertainment Mode Instead", use_container_width=True):
                    st.info("Switch to Entertainment Analytics for visual content analysis without transcript")
        
        # Chat Section
        elif selected == "💬 Chat":
            st.header("💬 AI Video Assistant")
            st.markdown(f"*Chat about this video with context for {st.session_state.audience} audience*")
            
            current_chat = st.session_state.chat_history.get(st.session_state.current_video['id'], [])
            
            # Display chat history
            for message in current_chat:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])
            
            # Chat input
            if prompt := st.chat_input("Ask anything about the video content..."):
                # Add user message
                st.session_state.chat_history.setdefault(st.session_state.current_video['id'], []).append({"role": "user", "content": prompt})
                
                with st.chat_message("user"):
                    st.markdown(prompt)
                
                with st.chat_message("assistant"):
                    with st.spinner("🤔 Analyzing and responding..."):
                        # Prepare context
                        context_parts = []
                        if st.session_state.transcript and st.session_state.transcript.get('translated'):
                            context_parts.append(f"Transcript: {st.session_state.transcript['translated'][:8000]}")
                        if st.session_state.analysis:
                            context_parts.append(f"Analysis: {json.dumps(st.session_state.analysis)}")
                        if st.session_state.video_info:
                            context_parts.append(f"Video Info: Title - {st.session_state.video_info.get('title', 'Unknown')}")
                            context_parts.append(f"Channel - {st.session_state.video_info.get('channel', 'Unknown')}")
                            context_parts.append(f"Description - {st.session_state.video_info.get('description', '')[:500]}")
                        
                        context = "\n\n".join(context_parts) if context_parts else "Basic video information available."
                        
                        response = content_model.generate_content(
                            f"You are a helpful AI assistant analyzing video content.\n\n"
                            f"Target Audience: {st.session_state.audience}\n"
                            f"Context: {context}\n\n"
                            f"User Question: {prompt}\n\n"
                            f"Provide a helpful, accurate response based on the available information. "
                            f"If information isn't available, suggest how to find it or offer general guidance."
                        )
                        
                        st.markdown(response.text)
                        st.session_state.chat_history[st.session_state.current_video['id']].append({"role": "assistant", "content": response.text})
            
            # Clear chat button
            if current_chat and st.button("🗑️ Clear Chat History", use_container_width=True):
                st.session_state.chat_history[st.session_state.current_video['id']] = []
                st.rerun()

else:
    # Welcome screen
    st.markdown("""
    <div style="text-align: center; padding: 50px;">
        <h2>🎬 Welcome to YouTube Video Insight AI</h2>
        <p style="font-size: 18px;">Your intelligent companion for extracting valuable insights from YouTube videos</p>
    </div>
    """, unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("""
        ### 📚 Knowledge Analytics Mode
        
        **Perfect for:**
        - Educational content
        - Tutorials and courses
        - Lectures and presentations
        - Technical explanations
        
        **Features:**
        - ✅ Learning roadmaps (works without transcript!)
        - ✅ Detailed summaries
        - ✅ Key topic extraction
        - ✅ Interactive Q&A
        """)
    
    with col2:
        st.markdown("""
        ### 🎬 Entertainment Analytics Mode
        
        **Perfect for:**
        - Music videos
        - Movie trailers
        - Gaming content
        - Vlogs and lifestyle
        
        **Features:**
        - ✅ Visual style analysis
        - ✅ Genre & mood detection
        - ✅ Content recommendations
        - ✅ Production quality assessment
        """)
    
    st.info("👈 **Get Started:** Enter a YouTube URL in the sidebar and select your analysis mode")
    
    with st.expander("💡 Pro Tips"):
        st.markdown("""
        **Even without transcripts, you can:**
        - Generate learning roadmaps from video metadata
        - Get AI-powered summaries based on title and description
        - Chat about the video content
        - Get audience-specific insights
        
        **For best results:**
        - Use Knowledge mode for educational content
        - Use Entertainment mode for visual content
        - Videos with clear titles and descriptions work better
        - Look for the CC icon for transcript availability
        """)