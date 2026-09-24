# EDA for the NBA-Analytics projection pipeline.
# Reads the Python pipeline's processed parquet via arrow -- one source of
# truth shared between the Python production code and R exploration.
# Writes PNGs to r/output/ (gitignored). See r/README.md for setup.

suppressPackageStartupMessages({
  library(arrow)
  library(dplyr)
  library(ggplot2)
  library(tidyr)
})

dir.create("r/output", showWarnings = FALSE, recursive = TRUE)

path <- "data/processed/historical_games.parquet"
if (!file.exists(path)) {
  stop("Run `python src/model/load_historical.py` first -- ", path, " not found.")
}
games <- read_parquet(path)

stopifnot(all(c("season", "player_id", "fantasy_points", "played",
                "rest_days", "back_to_back", "position") %in% names(games)))

played <- games %>% filter(played == 1)

# 1. League-wide scoring trend: mean fantasy points per player-game by season
#    (the model's target distribution drifting over time -- pace/era effects).
p1 <- played %>%
  group_by(season) %>%
  summarise(mean_fpts = mean(fantasy_points, na.rm = TRUE), .groups = "drop") %>%
  ggplot(aes(x = season, y = mean_fpts)) +
  geom_col(fill = "#FF4B2B") +
  labs(title = "Mean fantasy points per player-game, by season",
       x = NULL, y = "FPTS (default scoring)") +
  theme_minimal(base_size = 12) +
  theme(axis.text.x = element_text(angle = 45, hjust = 1))
ggsave("r/output/season_scoring_trend.png", p1, width = 9, height = 5, dpi = 120)

# 2. Rest effect: back-to-back vs full rest -- a real, pre-game-known signal
#    the model leans on (rest_days / back_to_back features).
p2 <- played %>%
  filter(!is.na(back_to_back)) %>%
  mutate(rest_group = ifelse(back_to_back == 1, "Back-to-back",
                             ifelse(rest_days <= 1, "1 day rest",
                                    "2+ days rest"))) %>%
  group_by(rest_group) %>%
  summarise(mean_fpts = mean(fantasy_points, na.rm = TRUE), .groups = "drop") %>%
  ggplot(aes(x = rest_group, y = mean_fpts, fill = rest_group)) +
  geom_col(show.legend = FALSE) +
  scale_fill_manual(values = c("Back-to-back" = "#E07A2E",
                               "1 day rest" = "#F5C542",
                               "2+ days rest" = "#2E9E63")) +
  labs(title = "Scoring by rest before the game",
       x = NULL, y = "FPTS") +
  theme_minimal(base_size = 12)
ggsave("r/output/rest_effect.png", p2, width = 8, height = 5, dpi = 120)

# 3. Position split (current-roster positions only -- historical players are
#    position UNK by design, see load_historical.py).
p3 <- played %>%
  filter(position != "UNK", position %in% c("G", "F", "C")) %>%
  ggplot(aes(x = position, y = fantasy_points, fill = position)) +
  geom_boxplot(outlier.alpha = 0.15) +
  scale_fill_manual(values = c("G" = "#7EA8FF", "F" = "#7FE0A8", "C" = "#FFB37F")) +
  coord_cartesian(ylim = c(0, 80)) +
  labs(title = "Fantasy point distribution by position",
       x = NULL, y = "FPTS") +
  theme_minimal(base_size = 12) + theme(legend.position = "none")
ggsave("r/output/position_splits.png", p3, width = 8, height = 5, dpi = 120)

# 4. Opportunity vs production: minutes vs fantasy points, with the naive
#    rolling-5 baseline as the reference the model must beat (color = season
#    recency so era drift is visible at a glance).
p4 <- played %>%
  filter(!is.na(min), min > 0, !is.na(fantasy_points)) %>%
  ggplot(aes(x = min, y = fantasy_points, colour = season)) +
  geom_point(alpha = 0.25, size = 0.8) +
  geom_smooth(method = "lm", se = FALSE, colour = "white", linewidth = 1) +
  labs(title = "Opportunity vs production (minutes vs FPTS)",
       x = "Minutes", y = "FPTS", colour = "Season") +
  theme_minimal(base_size = 12)
ggsave("r/output/opportunity_vs_production.png", p4,
       width = 9, height = 6, dpi = 120)

message("Wrote 4 plots to r/output/")
