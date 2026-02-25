"""90s-style TV Guide Channel renderer using PIL/Pillow.

Generates retro TV Guide graphics with:
- Vertical gradient backgrounds (navy blue → teal → purple)
- Color-coded channel bars
- Progress bars with chunky 90s styling
- Bold outlined text
- Digital clock display
- Authentic 90s aesthetic
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    pass


class TVGuideRenderer:
    """Renders 90s-style TV Guide graphics."""

    def __init__(
        self,
        width: int = 1920,
        height: int = 1080,
        debug: bool = False,
    ):
        """Initialize the TV Guide renderer.

        Args:
            width: Output image width in pixels
            height: Output image height in pixels
            debug: Enable debug logging
        """
        self.width = int(width)
        self.height = int(height)
        self.debug = bool(debug)

        # 90s Color palette
        self.colors = {
            # Background gradients
            "bg_navy": (25, 25, 80),  # Dark navy
            "bg_teal": (0, 139, 139),  # Dark cyan/teal
            "bg_purple": (106, 13, 173),  # Deep purple
            
            # Channel row backgrounds (cycling colors)
            "row_teal": (0, 139, 139),
            "row_purple": (139, 0, 255),
            "row_navy": (25, 25, 112),
            "row_orange": (255, 140, 0),
            "row_magenta": (199, 21, 133),
            
            # Text colors
            "text_white": (255, 255, 255),
            "text_yellow": (255, 215, 0),
            "text_cyan": (0, 255, 255),
            "text_orange": (255, 165, 0),
            "text_lime": (50, 255, 50),
            
            # Accents
            "outline_black": (0, 0, 0),
            "shadow_black": (0, 0, 0, 180),
            "progress_empty": (50, 50, 80),
            "progress_fill": (255, 215, 0),
        }

        # Layout dimensions
        self.header_height = 100
        self.footer_height = 80
        self.channel_row_height = 70
        self.margin = 20
        self.row_spacing = 5

        # Cached background to avoid regenerating
        self._cached_background: Image.Image | None = None

        # Try to load fonts (fallback to defaults if not available)
        self.fonts = self._load_fonts()

    def _load_fonts(self) -> dict[str, ImageFont.FreeTypeFont | ImageFont.ImageFont]:
        """Load fonts with fallbacks to system defaults."""
        fonts = {}
        
        # Font sizes
        title_size = 56
        clock_size = 48
        channel_size = 40
        episode_size = 28
        footer_size = 32
        
        # Try common font paths
        font_candidates = [
            "arial.ttf",
            "Arial.ttf",
            "arialbd.ttf",  # Arial Bold
            "DejaVuSans-Bold.ttf",
            "FreeSansBold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "C:\\Windows\\Fonts\\arialbd.ttf",
            "C:\\Windows\\Fonts\\arial.ttf",
        ]
        
        def try_load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
            """Try to load a TrueType font, fallback to default."""
            for font_path in font_candidates:
                try:
                    return ImageFont.truetype(font_path, size)
                except (OSError, IOError):
                    continue
            # Fallback to PIL default
            if self.debug:
                print(f"[debug] tvguide: using default font for size {size}")
            return ImageFont.load_default()
        
        fonts["title"] = try_load_font(title_size)
        fonts["clock"] = try_load_font(clock_size)
        fonts["channel"] = try_load_font(channel_size)
        fonts["episode"] = try_load_font(episode_size)
        fonts["footer"] = try_load_font(footer_size)
        
        return fonts

    def _create_gradient_background(self) -> Image.Image:
        """Create a vertical gradient background (navy → teal → purple)."""
        if self._cached_background is not None:
            return self._cached_background.copy()
        
        img = Image.new("RGB", (self.width, self.height))
        draw = ImageDraw.Draw(img)
        
        # Three-color gradient: navy (top) → teal (middle) → purple (bottom)
        navy = self.colors["bg_navy"]
        teal = self.colors["bg_teal"]
        purple = self.colors["bg_purple"]
        
        # Split into two gradient sections
        mid_point = self.height // 2
        
        # Top half: navy → teal
        for y in range(mid_point):
            ratio = y / mid_point
            r = int(navy[0] + (teal[0] - navy[0]) * ratio)
            g = int(navy[1] + (teal[1] - navy[1]) * ratio)
            b = int(navy[2] + (teal[2] - navy[2]) * ratio)
            draw.line([(0, y), (self.width, y)], fill=(r, g, b))
        
        # Bottom half: teal → purple
        for y in range(mid_point, self.height):
            ratio = (y - mid_point) / (self.height - mid_point)
            r = int(teal[0] + (purple[0] - teal[0]) * ratio)
            g = int(teal[1] + (purple[1] - teal[1]) * ratio)
            b = int(teal[2] + (purple[2] - teal[2]) * ratio)
            draw.line([(0, y), (self.width, y)], fill=(r, g, b))
        
        # Cache it
        self._cached_background = img.copy()
        return img

    def _draw_outlined_text(
        self,
        draw: ImageDraw.ImageDraw,
        position: tuple[int, int],
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        fill_color: tuple[int, int, int],
        outline_color: tuple[int, int, int] = (0, 0, 0),
        outline_width: int = 3,
    ) -> None:
        """Draw text with thick outline (very 90s style)."""
        x, y = position
        
        # Draw outline by drawing text multiple times with offset
        for offset_x in range(-outline_width, outline_width + 1):
            for offset_y in range(-outline_width, outline_width + 1):
                if offset_x != 0 or offset_y != 0:
                    draw.text((x + offset_x, y + offset_y), text, font=font, fill=outline_color)
        
        # Draw main text on top
        draw.text((x, y), text, font=font, fill=fill_color)

    def _draw_tv_icon(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        size: int = 50,
    ) -> None:
        """Draw a simple retro TV icon."""
        # TV screen (rectangle)
        screen_margin = 4
        draw.rectangle(
            [x + screen_margin, y + screen_margin, x + size - screen_margin, y + size - screen_margin],
            fill=self.colors["text_cyan"],
            outline=self.colors["text_yellow"],
            width=3,
        )
        
        # Antenna (V-shape on top)
        antenna_base_y = y
        antenna_height = 8
        antenna_left_x = x + size // 3
        antenna_right_x = x + (2 * size) // 3
        
        # Left antenna
        draw.line(
            [(x + size // 2, antenna_base_y), (antenna_left_x, antenna_base_y - antenna_height)],
            fill=self.colors["text_yellow"],
            width=3,
        )
        # Right antenna
        draw.line(
            [(x + size // 2, antenna_base_y), (antenna_right_x, antenna_base_y - antenna_height)],
            fill=self.colors["text_yellow"],
            width=3,
        )

    def _draw_header(
        self,
        draw: ImageDraw.ImageDraw,
        current_time: datetime,
    ) -> None:
        """Draw the header section with title and clock."""
        # Header background (semi-transparent dark overlay)
        header_bg = Image.new("RGBA", (self.width, self.header_height), (0, 0, 0, 200))
        
        # Draw retro TV icon
        icon_x = self.margin + 10
        icon_y = (self.header_height - 50) // 2
        self._draw_tv_icon(draw, icon_x, icon_y, size=50)
        
        # Title: "TV GUIDE" on the left (after icon)
        title_text = "TV GUIDE"
        title_x = icon_x + 70
        title_y = (self.header_height - 56) // 2
        
        self._draw_outlined_text(
            draw,
            (title_x, title_y),
            title_text,
            self.fonts["title"],
            self.colors["text_yellow"],
            outline_width=4,
        )
        
        # Clock on the right with timezone
        clock_text = current_time.strftime("%I:%M %p CST")
        
        # Measure text to right-align
        bbox = draw.textbbox((0, 0), clock_text, font=self.fonts["clock"])
        clock_width = bbox[2] - bbox[0]
        clock_x = self.width - clock_width - self.margin - 60
        clock_y = (self.header_height - 48) // 2
        
        self._draw_outlined_text(
            draw,
            (clock_x, clock_y),
            clock_text,
            self.fonts["clock"],
            self.colors["text_cyan"],
            outline_width=4,
        )

    def _draw_channel_row(
        self,
        draw: ImageDraw.ImageDraw,
        img: Image.Image,
        y_position: int,
        call_sign: str,
        episode: str,
        percent_complete: float,
        row_index: int,
    ) -> None:
        """Draw a single channel row with retro styling."""
        # Cycle through background colors
        bg_colors = [
            self.colors["row_teal"],
            self.colors["row_purple"],
            self.colors["row_navy"],
            self.colors["row_orange"],
            self.colors["row_magenta"],
        ]
        text_colors = [
            self.colors["text_yellow"],
            self.colors["text_cyan"],
            self.colors["text_orange"],
            self.colors["text_white"],
            self.colors["text_lime"],
        ]
        
        bg_color = bg_colors[row_index % len(bg_colors)]
        text_color = text_colors[row_index % len(text_colors)]
        
        # Row background with rounded corners effect (90s style)
        row_x1 = self.margin
        row_y1 = y_position
        row_x2 = self.width - self.margin
        row_y2 = y_position + self.channel_row_height
        
        # Draw main rectangle
        draw.rectangle([row_x1, row_y1, row_x2, row_y2], fill=bg_color)
        
        # Add 3D effect (lighter top edge, darker bottom edge)
        lighter = tuple(min(255, c + 30) for c in bg_color)
        darker = tuple(max(0, c - 30) for c in bg_color)
        draw.line([row_x1, row_y1, row_x2, row_y1], fill=lighter, width=2)
        draw.line([row_x1, row_y2, row_x2, row_y2], fill=darker, width=2)
        
        # Channel call sign (left side, bold)
        channel_x = row_x1 + 15
        channel_y = y_position + (self.channel_row_height - 40) // 2
        
        self._draw_outlined_text(
            draw,
            (channel_x, channel_y),
            call_sign,
            self.fonts["channel"],
            self.colors["text_white"],
            outline_width=3,
        )
        
        # Episode name (center)
        # Truncate if too long
        episode_truncated = episode[:45] + "..." if len(episode) > 45 else episode
        episode_x = channel_x + 150
        episode_y = y_position + (self.channel_row_height - 28) // 2
        
        self._draw_outlined_text(
            draw,
            (episode_x, episode_y),
            episode_truncated,
            self.fonts["episode"],
            text_color,
            outline_width=2,
        )
        
        # Progress bar (right side, chunky 90s style)
        progress_width = 300
        progress_height = 30
        progress_x = row_x2 - progress_width - 20
        progress_y = y_position + (self.channel_row_height - progress_height) // 2
        
        # Progress background (dark)
        draw.rectangle(
            [progress_x, progress_y, progress_x + progress_width, progress_y + progress_height],
            fill=self.colors["progress_empty"],
            outline=self.colors["outline_black"],
            width=2,
        )
        
        # Progress fill (chunky blocks for 90s effect)
        if percent_complete > 0:
            fill_width = int((progress_width - 4) * (percent_complete / 100.0))
            # Draw as blocks
            block_width = 12
            num_blocks = fill_width // block_width
            for i in range(num_blocks):
                block_x = progress_x + 2 + (i * block_width)
                draw.rectangle(
                    [block_x, progress_y + 2, block_x + block_width - 2, progress_y + progress_height - 2],
                    fill=self.colors["progress_fill"],
                )
        
        # Percentage text overlay
        percent_text = f"{int(percent_complete)}%"
        bbox = draw.textbbox((0, 0), percent_text, font=self.fonts["episode"])
        percent_width = bbox[2] - bbox[0]
        percent_x = progress_x + (progress_width - percent_width) // 2
        percent_y = progress_y + (progress_height - 28) // 2 - 2
        
        self._draw_outlined_text(
            draw,
            (percent_x, percent_y),
            percent_text,
            self.fonts["episode"],
            self.colors["text_white"],
            outline_width=2,
        )

    def _draw_footer(
        self,
        draw: ImageDraw.ImageDraw,
    ) -> None:
        """Draw the footer section with instructions."""
        footer_y = self.height - self.footer_height
        
        # Footer background (semi-transparent dark overlay)
        draw.rectangle(
            [0, footer_y, self.width, self.height],
            fill=(0, 0, 0, 200),
        )
        
        # Instruction text (centered)
        footer_text = "◄◄◄  PRESS CHANNEL UP/DOWN TO EXIT GUIDE  ►►►"
        
        bbox = draw.textbbox((0, 0), footer_text, font=self.fonts["footer"])
        text_width = bbox[2] - bbox[0]
        text_x = (self.width - text_width) // 2
        text_y = footer_y + (self.footer_height - 32) // 2
        
        self._draw_outlined_text(
            draw,
            (text_x, text_y),
            footer_text,
            self.fonts["footer"],
            self.colors["text_yellow"],
            outline_width=3,
        )

    def render(
        self,
        guide_data: list[dict],
        current_time: datetime | None = None,
    ) -> bytes:
        """Render the TV guide as a PNG image (BGRA format for mpv).

        Args:
            guide_data: List of dicts with keys: call_sign, episode, percent_complete
            current_time: Current time for clock display (defaults to now)

        Returns:
            PNG image data as bytes in BGRA format
        """
        if current_time is None:
            current_time = datetime.now()
        
        # Create base image with gradient background
        img = self._create_gradient_background()
        draw = ImageDraw.Draw(img)
        
        # Draw header
        self._draw_header(draw, current_time)
        
        # Calculate channel list area
        list_start_y = self.header_height + self.margin
        list_end_y = self.height - self.footer_height - self.margin
        list_height = list_end_y - list_start_y
        
        # Calculate how many rows we can fit
        total_row_height = self.channel_row_height + self.row_spacing
        max_visible_rows = int(list_height // total_row_height)
        
        # Draw channel rows (limit to visible area)
        visible_data = guide_data[:max_visible_rows]
        
        for i, channel_info in enumerate(visible_data):
            y_pos = list_start_y + (i * total_row_height)
            
            self._draw_channel_row(
                draw,
                img,
                y_pos,
                channel_info["call_sign"],
                channel_info["episode"],
                channel_info["percent_complete"],
                i,
            )
        
        # Draw footer
        self._draw_footer(draw)
        
        # Convert to BGRA for mpv overlay-add
        # mpv expects raw BGRA pixel data
        img_rgba = img.convert("RGBA")
        
        # Convert RGBA to BGRA by swapping R and B channels
        r, g, b, a = img_rgba.split()
        img_bgra = Image.merge("RGBA", (b, g, r, a))
        
        # Return raw bytes
        return img_bgra.tobytes()
    
    def render_to_file(
        self,
        guide_data: list[dict],
        output_path: str | Path,
        current_time: datetime | None = None,
        scroll_offset: float = 0.0,
    ) -> None:
        """Render the TV guide and save as PNG file with smooth scrolling.

        Args:
            guide_data: List of dicts with keys: call_sign, episode, percent_complete
            output_path: Path to save the PNG file
            current_time: Current time for clock display (defaults to now)
            scroll_offset: Vertical scroll offset in rows (0.0 = no scroll, 1.0 = scroll by 1 row, etc.)
        """
        if current_time is None:
            current_time = datetime.now()
        
        # Create base image with gradient background
        img = self._create_gradient_background()
        draw = ImageDraw.Draw(img)
        
        # Draw header (always visible, not affected by scroll)
        self._draw_header(draw, current_time)
        
        # Calculate channel list area
        list_start_y = self.header_height + self.margin
        list_end_y = self.height - self.footer_height - self.margin
        list_height = list_end_y - list_start_y
        
        # Calculate how many rows we can fit
        total_row_height = self.channel_row_height + self.row_spacing
        max_visible_rows = int(list_height // total_row_height)
        
        # Apply scroll offset (smooth fractional scrolling)
        scroll_offset_px = int(scroll_offset * total_row_height)
        
        # Calculate which channels to show based on scroll
        # We need to show extra rows to handle partial visibility
        start_index = int(scroll_offset)
        num_to_render = max_visible_rows + 2  # Extra rows for smooth scroll
        
        # Wrap around if needed (continuous loop)
        visible_indices = []
        for i in range(num_to_render):
            index = (start_index + i) % len(guide_data)
            visible_indices.append(index)
        
        # Draw channel rows with scroll offset
        for i, data_index in enumerate(visible_indices):
            # Calculate Y position with scroll offset applied
            y_pos = list_start_y + (i * total_row_height) - scroll_offset_px
            
            # Only draw if at least partially visible
            if y_pos + self.channel_row_height > list_start_y and y_pos < list_end_y:
                channel_info = guide_data[data_index]
                
                self._draw_channel_row(
                    draw,
                    img,
                    y_pos,
                    channel_info["call_sign"],
                    channel_info["episode"],
                    channel_info["percent_complete"],
                    data_index,  # Use actual index for color cycling
                )
        
        # Draw footer (always visible, not affected by scroll)
        self._draw_footer(draw)
        
        # Save as PNG
        img.save(str(output_path), "PNG")
    
    def get_dimensions(self) -> tuple[int, int]:
        """Return the output dimensions (width, height)."""
        return (self.width, self.height)
