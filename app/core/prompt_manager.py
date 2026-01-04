"""
Prompt Template Manager using Jinja2
Manages loading and rendering of Markdown-formatted prompt templates
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, Template, TemplateNotFound

from app.core.logger_config import logger


class PromptManager:
    """
    Manages Jinja2 prompt templates stored as Markdown files

    Templates are stored in app/prompts/ directory with .md extension
    """

    def __init__(self):
        """Initialize the prompt manager with template directory"""
        self.prompts_dir = Path(__file__).parent.parent / "prompts"

        if not self.prompts_dir.exists():
            raise FileNotFoundError(f"Prompts directory not found: {self.prompts_dir}")

        # Initialize Jinja2 environment
        self.env = Environment(
            loader=FileSystemLoader(str(self.prompts_dir)),
            autoescape=False,  # We're generating prompts, not HTML
            trim_blocks=True,
            lstrip_blocks=True,
        )

        logger.info(f"PromptManager initialized with directory: {self.prompts_dir}")

    def render(self, template_name: str, **kwargs: Any) -> str:
        """
        Render a template with the given variables

        Automatically adds CURRENT_TIME variable to all templates

        Args:
            template_name: Name of the template (without .md extension)
            **kwargs: Variables to pass to the template

        Returns:
            Rendered template as string

        Raises:
            TemplateNotFound: If template file doesn't exist
        """
        template_file = f"{template_name}.md"

        try:
            # Automatically add CURRENT_TIME variable
            template_vars = {
                "CURRENT_TIME": datetime.now().strftime("%a %b %d %Y %H:%M:%S %z"),
                **kwargs,
            }

            template = self.env.get_template(template_file)
            rendered = template.render(**template_vars)

            logger.debug(
                f"Rendered template '{template_name}' "
                f"with {len(template_vars)} variables (including CURRENT_TIME)"
            )

            return rendered

        except TemplateNotFound:
            logger.error(f"Template not found: {template_file}")
            raise
        except Exception as e:
            logger.error(f"Error rendering template '{template_name}': {e}")
            raise

    def list_templates(self) -> list[str]:
        """
        List all available template names

        Returns:
            List of template names (without .md extension)
        """
        templates = []
        for file_path in self.prompts_dir.glob("*.md"):
            template_name = file_path.stem
            templates.append(template_name)

        return sorted(templates)

    def template_exists(self, template_name: str) -> bool:
        """
        Check if a template exists

        Args:
            template_name: Name of the template (without .md extension)

        Returns:
            True if template exists, False otherwise
        """
        template_file = self.prompts_dir / f"{template_name}.md"
        return template_file.exists()


# Global instance for easy access
prompt_manager = PromptManager()
