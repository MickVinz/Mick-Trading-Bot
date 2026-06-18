```markdown
# Mick-Trading-Bot Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill teaches you the core development patterns and workflows used in the Mick-Trading-Bot Python codebase. You'll learn how to implement new features, add multi-coin support, follow the project's coding conventions, and write and organize tests. The repository is structured for clarity and maintainability, with a focus on modular Python scripts and clear workflow automation.

## Coding Conventions

- **File Naming:**  
  Use `snake_case` for all Python files and modules.  
  _Example:_  
  ```
  src/config_utils.py
  scripts/test_paper_engine.py
  ```

- **Import Style:**  
  Use **relative imports** within the package.  
  _Example:_  
  ```python
  from .journal import Journal
  from . import config_utils
  ```

- **Export Style:**  
  Use **named exports** (explicit function and class definitions).  
  _Example:_  
  ```python
  def load_config(path):
      ...
  
  class PaperEngine:
      ...
  ```

- **Commit Messages:**  
  Follow [Conventional Commits](https://www.conventionalcommits.org/) with the `feat` prefix for new features.  
  _Example:_  
  ```
  feat: add multi-coin support to paper engine
  ```

## Workflows

### Feature Implementation with Tests
**Trigger:** When you want to add a new feature or major enhancement to the codebase  
**Command:** `/new-feature-with-tests`

1. **Implement feature logic** in the `src/` directory.  
   _Example:_  
   ```
   src/paper/paper_engine.py
   ```
2. **Create or update the corresponding test script** in the `scripts/` directory.  
   _Example:_  
   ```
   scripts/test_paper_engine.py
   ```
3. **Run the tests** to ensure correctness.  
   _Example:_  
   ```bash
   python scripts/test_paper_engine.py
   ```
4. **Commit your changes** with a conventional commit message.  
   _Example:_  
   ```
   feat: add stop-loss logic to paper engine
   ```

### Multi-Coin Feature Rollout
**Trigger:** When you want to extend the system to support multiple coins/assets  
**Command:** `/enable-multi-coin`

1. **Update `config/config.yaml`** to add new coins or overrides.  
   _Example:_  
   ```yaml
   coins:
     - BTC
     - ETH
     - SOL
   ```
2. **Implement or update migration scripts** as needed.  
   _Example:_  
   ```
   scripts/migrate_state_v2.py
   ```
3. **Update dashboard or UI scripts** to reflect new coins.  
   _Example:_  
   ```
   scripts/dashboard.html
   ```
4. **Document the feature** in the appropriate documentation directories.  
   _Example:_  
   ```
   docs/superpowers/plans/multi_coin.md
   docs/superpowers/specs/multi_coin_support.md
   ```
5. **Test the new functionality** and ensure all documentation is up to date.

## Testing Patterns

- **Test File Naming:**  
  Test scripts are placed in the `scripts/` directory and follow the pattern:  
  ```
  scripts/test_*.py
  ```
  _Example:_  
  ```
  scripts/test_config_utils.py
  scripts/test_journal_multicoin.py
  ```

- **Framework:**  
  No specific testing framework is enforced; tests are typically standalone Python scripts.

- **How to Run:**  
  Execute test scripts directly with Python:  
  ```bash
  python scripts/test_config_utils.py
  ```

## Commands

| Command                 | Purpose                                               |
|-------------------------|-------------------------------------------------------|
| /new-feature-with-tests | Start a new feature implementation with tests         |
| /enable-multi-coin      | Roll out or update multi-coin support                |
```
