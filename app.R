options(shiny.maxRequestSize = 100*1024^2)

# List of required packages
required_packages <- c(
  "shiny", "ggplot2", "dplyr", "scales", "viridis",
  "RColorBrewer", "plotly", "segmented", "shinythemes", 
  "shinycssloaders", "rstatix", "boot", "caret","ggpubr",
  "DT", "mgcv", "patchwork"
)

# Function to check and install missing packages
install_if_missing <- function(pkg) {
  if (!requireNamespace(pkg, quietly = TRUE)) {
    message(paste("Installing missing package:", pkg))
    install.packages(pkg)
  }
}

# Check all packages
invisible(lapply(required_packages, install_if_missing))

# Load all libraries
lapply(required_packages, library, character.only = TRUE)

library(shiny)
library(ggplot2)
library(dplyr)
library(scales)
library(viridis)
library(RColorBrewer)
library(plotly)
library(segmented)
library(shinythemes)
library(shinycssloaders)
library(boot)
library(caret)
library(rstatix)
library(boot)
library(ggpubr)
library(DT)
library(mgcv)
library(patchwork)

# UI - Design -------

ui <- navbarPage(
  title = "Ice Nucleation Data Explorer",
  theme = shinytheme("slate"),
  
  # 1) Import Fonts From Google Fonts
  tags$head(
    # Google Fonts
    tags$link(
      rel = "stylesheet",
      href = "https://fonts.googleapis.com/css2?family=Raleway:wght@400;700&display=swap"
    ),
    
    # Apply font
    tags$style(HTML("
    body, h1, h2, h3, h4, h5, h6,
    label, input, button, select, textarea,
    .navbar, .navbar-brand, .nav > li > a,
    .sidebarPanel, .well, .tabbable {
      font-family: 'Raleway', sans-serif !important;
    }
  ")),
    
    # Input controls styling
    tags$style(HTML("
    .form-control {
      background-color: #3c3f41;
      color: #ffffff;
      border: 1px solid #555555;
    }
    .form-control:focus {
      background-color: #3c3f41;
      color: #ffffff;
      border-color: #888888;
      box-shadow: none;
    }
    .selectize-input {
      background-color: #3c3f41 !important;
      color: #ffffff !important;
      border: 1px solid #555555 !important;
    }
    .selectize-input.focus {
      border-color: #888888 !important;
      box-shadow: none !important;
    }
    .selectize-dropdown {
      background-color: #3c3f41 !important;
      color: #ffffff !important;
      border: 1px solid #555555 !important;
    }
    .selectize-dropdown .active {
      background-color: #555555 !important;
      color: #ffffff !important;
    }
  ")),
    
    tags$style(HTML("
    .dataTables_wrapper .dataTables_length,
    .dataTables_wrapper .dataTables_filter,
    .dataTables_wrapper .dataTables_info,
    .dataTables_wrapper .dataTables_paginate,
    table.dataTable tbody td {
      color: white !important;
    }
  "))
  ),
  
  # Always show the file upload at top
  fluidRow(
    column(12,
           fileInput("file_input", "Upload data file (.txt or .csv)", accept = c(".txt",".csv")),
           fileInput("file_metadata", "Upload metadata file (.csv)", accept = c(".csv"))
    )
  ),
  ## —————————
  ## Tab 1: Freezing Curves ------
  ## —————————
  tabPanel("Freezing Curves",
           sidebarLayout(
             sidebarPanel(
               selectInput("Size_filter", "Select particle size:",
                           choices = c("b_5_m","b_02_m"),
                           selected = c("b_5_m","b_02_m"), multiple = TRUE),
               uiOutput("dilution_selector"),
               uiOutput("location_selector"),
               selectInput("palette_select", "Color palette:",
                           choices = c("viridis","magma","plasma","Dark2","Set1","Set2","Set3"),
                           selected = "viridis"),
               numericInput("stroke_width", "Point border width:",
                            value = 0.3, min = 0.1, max = 2, step = 0.1),
               textInput("curve_title", "Plot title:", "Freezing Curves"),
               textInput("curve_subtitle", "Plot subtitle:", ""),
               uiOutput("shape_selector"),
               checkboxInput("show_grid", "Show grid lines", value = TRUE),
               actionButton("update_plot", "Update Curves", icon = icon("sync")),  
             ),
             mainPanel(
               plotlyOutput("curvePlot", height = "600px")
             )
           )
  ),
  
  ## —————————
  ## Tab 2: Compare Samples FC ------
  ## —————————
  tabPanel("Compare Samples FC",
           sidebarLayout(
             sidebarPanel(
               selectizeInput("selected_samples_cmp", "Select up to 3 Samples:",
                              choices = NULL,
                              selected = NULL,
                              multiple = TRUE,
                              options = list(maxItems = 3)),
               selectInput("Size_filter_cmp", "Select particle size:",
                           choices = c("b_5_m","b_02_m"),
                           selected = c("b_5_m","b_02_m"), multiple = TRUE),
               uiOutput("dilution_selector_cmp"),
               selectInput("palette_select_cmp", "Color palette:",
                           choices = c("viridis","magma","plasma","Dark2","Set1","Set2","Set3"),
                           selected = "viridis"),
               numericInput("stroke_width_cmp", "Point border width:",
                            value = 0.3, min = 0.1, max = 2, step = 0.1),
               textInput("curve_title_cmp", "Plot title:", "Compare Selected Samples"),
               textInput("curve_subtitle_cmp", "Plot subtitle:", ""),
               uiOutput("shape_selector_cmp"),
               checkboxInput("show_grid_cmp", "Show grid lines", value = TRUE),
               actionButton("update_plot_cmp", "Update Curves", icon = icon("sync"))
             ),
             mainPanel(
               plotlyOutput("curvePlot_cmp", height = "600px")
             )
           )
  ),
  
  ## —————————
  ## Tab 3: Taxa Analysis ------
  ## —————————
  tabPanel("Taxa Analysis",
           sidebarLayout(
             sidebarPanel(
               fileInput("asv_file", "Upload ASV table (with taxonomy)", accept = ".csv"),
               selectInput("tax_level", "Select taxonomic level:",
                           choices = c("Phylum", "Class", "Order", "Family", "Genus", "Species"),
                           selected = "Genus"),
               radioButtons("analysis_type", "Analysis mode:",
                            choices = c("Per Sample", "Per Location"),
                            selected = "Per Sample", inline = TRUE),
               uiOutput("sample_selectors")
             ),
             mainPanel(
               h4("Top 30 taxa per sample"),
               fluidRow(
                 column(6,
                        uiOutput("title_sample1"),
                        DTOutput("top_taxa_1")
                 ),
                 column(6,
                        uiOutput("title_sample2"),
                        DTOutput("top_taxa_2")
                 )
               ),
               hr(),
               h4("Presence/Absence Comparison"),
               DTOutput("presence_absence_table"),
               br(),
               plotOutput("presence_barplot", height = "300px"),
               br()
             )
           )
  ),
  
  ## —————————
  ## Tab 4: Frozen Fraction --------
  ## —————————
  tabPanel("Frozen Fraction",
           sidebarLayout(
             sidebarPanel(
               uiOutput("sample_selector"),
               selectInput("Size_filter_ff", "Select Size:",
                           choices = NULL, multiple = TRUE),
               selectInput("palette_select_ff", "Color palette:",
                           choices = c("viridis","magma","plasma"),
                           selected = "viridis"),
               checkboxInput("show_control", "Show control points", value = TRUE),
               uiOutput("shape_selector_ff"),
               textInput("ff_title", "Plot title:", "Frozen Fraction"),
               textInput("ff_subtitle", "Plot subtitle:", ""),
               actionButton("update_ff", "Update FF Plot", icon = icon("sync")),
             ),
             mainPanel(
               plotlyOutput("ffPlot", height = "600px")
             )
           )
  ),
  
  ## —————————
  ## Tab 5: Kneepoint Analysis --------
  ## —————————
  tabPanel("Kneepoint Analysis",
           sidebarLayout(
             sidebarPanel(
               uiOutput("sample_ui_pw"),
               uiOutput("Size_ui_pw"),
               uiOutput("dilution_ui_pw"),
               numericInput("n_breaks_pw", "Number of INP families (breakpoints):",
                            value = 1, min = 1, max = 5, step = 1),
               sliderInput(
                 "spar_pw",
                 "Spline smoothness (spar):",
                 min   = 0.1,
                 max   = 1.5,
                 value = 1,
                 step  = 0.05
               ),
               checkboxGroupInput("display_layers_pw", "Display layers:",
                                  choices = c("Curve","spline","Piecewise","Show KP","spline CI"),
                                  selected = c("Curve","spline","Piecewise")),
               selectInput("ci_spline_palette", "spline CI palette:",
                           choices = c("Set1","Set2","Set3","Dark2","Paired","Accent"),
                           selected = "Set2"),
               textInput("plot_title_pw", "Plot Title:", "Freezing Curve Analysis"),
               textInput("plot_subtitle_pw", "Plot Subtitle:", ""),
               actionButton("go_pw", "Run Analysis"),
               actionButton("run_stats", "Run Statistical Analysis", icon = icon("cogs"))
             ),
             mainPanel(
               verbatimTextOutput("kp_values_pw"),
               plotlyOutput("plot_piecewise_pw", height = "600px"),
               
               # only after hitting “Run Statistical Analysis”
               conditionalPanel(
                 condition = "input.run_stats > 0",
                 tags$h4("Parametric Confidence Intervals (CI)", style = "margin-top: 20px; font-weight: bold;"),
                 tableOutput("ci_table"),
                 
                 tags$h4("ANOVA vs Linear Model", style = "margin-top: 20px; font-weight: bold;"),
                 tableOutput("anova_table"),
                 
                 tags$h4("Bootstrap CI for Breakpoints (Percentile)", style = "margin-top: 20px; font-weight: bold;"),
                 tableOutput("bootstrap_table"),
                 
                 tags$h4("5-Fold Cross-Validation", style = "margin-top: 20px; font-weight: bold;"),
                 tableOutput("cv_table"),
                 downloadButton("download_heavy_stats", "Download Heavy Statistics (CSV ZIP)")
               ) %>% withSpinner(),
               
               plotOutput("resid_diag_pw") %>% withSpinner()
             )
           )
           
  ),
  ## —————————
  ## Tab 6: Boxplot Comparison --------
  ## —————————
  tabPanel("Boxplot Comparison",
           sidebarLayout(
             sidebarPanel(
               # 1) Choose which nM variable: "nM_10" or "nM_15"
               selectInput(
                 "nm_choice",
                 "Select nM variable:",
                 choices = c("nM_10", "nM_15"),
                 selected = "nM_10"
               ),
               
               # 2) Choose which Size: "b_5_m" or "b_02_m"
               selectInput(
                 "size_choice",
                 "Select Size:",
                 choices = c("b_5_m", "b_02_m"),
                 selected = "b_5_m"
               ),
               
               # 3) Choose the column to compare (populated dynamically)
               uiOutput("comparison_column_ui"),
               
               selectInput("boxplot_palette", "Color palette:",
                           choices = c("Pastel1", "Set2", "Set3", "Dark2", "Paired", "viridis"),
                           selected = "Pastel1"),
               
               # 4) Choose grouping
               uiOutput("binning_options"),
               tableOutput("binning_info"),
               
               # 5) Button to generate the boxplot
               actionButton("generate_boxplot", "Generate Boxplot"),
               
               # 6) Button to run statistical analysis
               actionButton("run_boxplot_stats", "Run Statistical Analysis", icon = icon("cogs"))
             ),
             mainPanel(
               
               # 5) Boxplot output
               plotlyOutput("boxplot_comparison", height = "600px"),
               
               # Output for statistical results
               uiOutput("boxplot_stats") %>% withSpinner(),
               
               downloadButton("download_stats", "Download Statistical Results (CSV)"),
               
               # Conditional panel for diagnostic plots
               conditionalPanel(
                 condition = "input.run_boxplot_stats > 0",
                 plotOutput("residual_diag_boxplot") %>% withSpinner()
               )
             )
           )
  ),
  # Tab 7: Correlations Analysis --------
  tabPanel("Correlations Analysis",
           sidebarLayout(
             sidebarPanel(
               selectInput("analysis_method", "Analysis Method:",
                           choices = c("Spearman", "Pearson", "Quadratic Fit", "GAM"),
                           selected = "Spearman"),
               selectInput("spearman_var", "Select variable for correlation:",
                           choices = NULL),
               actionButton("run_spearman", "Run Analysis"),
               downloadButton("download_spearman", "Download Results")
             ),
             mainPanel(
               conditionalPanel(
                 "input.run_spearman > 0", 
                 plotOutput("plot_nM10", height = "400px"),
                 plotOutput("plot_nM15", height = "400px"),
                 
                 # download buttons for high-res PNGs
                 downloadButton("download_plot_nM10", "Download nM\u2081\u2080 Plot (PNG)"),
                 downloadButton("download_plot_nM15", "Download nM\u2081\u2085 Plot (PNG)")
               )
             )
           )
  )
)

server <- function(input, output, session) {
  
  go_flag <- eventReactive(input$go_pw, {
    Sys.time()
  })
  # —————————
  # 1) DATA ------------
  # —————————
  data_raw <- reactive({
    req(input$file_input)
    ext <- tools::file_ext(input$file_input$name)
    df <- if(ext == "csv") {
      read.csv(input$file_input$datapath, stringsAsFactors = FALSE)
    } else {
      read.delim2(input$file_input$datapath,
                  na.strings = c("#N/A","#NUMMER!"))
    }
    df %>% mutate(
      nm = as.numeric(nm),
      Freezing.temperature = as.numeric(Freezing.temperature),
      FF = as.numeric(FF),
      Sample = factor(Sample),
      Sample_ID = factor(Sample_ID),
      Location = factor(Location),
      Dilution.factor = factor(Dilution.factor),
      Size = factor(Size),
      Control = factor(Control)
    )
  })
  
  metadata_raw <- reactive({
    req(input$file_metadata)
    read.csv(input$file_metadata$datapath, stringsAsFactors = FALSE)
  })
  
  metadata_with_nm <- reactive({
    req(metadata_raw(), data_raw())
    
    df <- data_raw() %>%
      mutate(
        nm = as.numeric(nm),
        Freezing.temperature = as.numeric(Freezing.temperature)
      ) %>%
      filter(!is.na(nm), !is.na(Freezing.temperature), !is.na(Sample), !is.na(Size)) %>%
      group_by(Sample) %>%
      arrange(desc(Freezing.temperature)) %>%
      slice((ceiling(n() * 0.05) + 1):(floor(n() * 0.95))) %>%
      ungroup()
    
    sample_size_combinations <- df %>% distinct(Sample, Size)
    results_list <- list()
    
    for (i in 1:nrow(sample_size_combinations)) {
      sample_name <- sample_size_combinations$Sample[i]
      size_name <- sample_size_combinations$Size[i]
      
      sample_data <- df %>%
        filter(Sample == sample_name, Size == size_name)
      
      if (nrow(sample_data) < 10) next
      
      loess_model <- loess(nm ~ Freezing.temperature, data = sample_data, span = 0.1)
      pred_10 <- predict(loess_model, data.frame(Freezing.temperature = -10))
      pred_15 <- predict(loess_model, data.frame(Freezing.temperature = -15))
      
      results_list[[paste(sample_name, size_name, sep = "_")]] <- tibble(
        Sample = sample_name,
        Size = size_name,
        nM_10 = ifelse(pred_10 < 0, NA, pred_10),
        nM_15 = ifelse(pred_15 < 0, NA, pred_15)
      )
    }
    
    nm_results <- bind_rows(results_list)
    
    nm_summarised <- nm_results %>%
      group_by(Sample, Size) %>%
      summarise(
        nM_10 = mean(nM_10, na.rm = TRUE),
        nM_15 = mean(nM_15, na.rm = TRUE),
        .groups = "drop"
      )
    
    nm_b5 <- nm_summarised %>%
      filter(Size == "b_5_m") %>%
      select(Sample, nM_10, nM_15) %>%
      rename(nM10_b5 = nM_10, nM15_b5 = nM_15)
    
    nm_b02 <- nm_summarised %>%
      filter(Size == "b_02_m") %>%
      select(Sample, nM_10, nM_15) %>%
      rename(nM10_b02 = nM_10, nM15_b02 = nM_15)
    
    metadata_raw() %>%
      left_join(nm_b5, by = "Sample") %>%
      left_join(nm_b02, by = "Sample")
  })
  
  ## 2) FREEZING CURVES ------
  output$dilution_selector <- renderUI({ req(data_raw())
    selectInput("dilution_select","Select dilutions:",
                choices = unique(data_raw()$Dilution.factor),
                selected = unique(data_raw()$Dilution.factor),
                multiple = TRUE)
  })
  output$location_selector <- renderUI({ req(data_raw())
    selectInput("location_select","Select locations:",
                choices = unique(data_raw()$Location),
                selected = unique(data_raw()$Location),
                multiple = TRUE)
  })
  output$shape_selector <- renderUI({
    req(input$location_select)
    lapply(input$location_select, function(loc) {
      selectInput(paste0("shape_",loc),
                  paste("Shape for",loc),
                  choices = c(Circle=21,Square=22,
                              Triangle=24,Diamond=23),
                  selected = 21)
    })
  })
  selected_shapes <- reactive({
    # 1) Calculate vector of numeric values
    shapes <- sapply(input$location_select, function(loc) {
      input[[paste0("shape_", loc)]]
    })
    # 2) Associate to each value the correspondent location
    names(shapes) <- input$location_select
    shapes
  })
  filtered_curves <- debounce(reactive({
    data_raw() %>%
      filter(Size %in% input$Size_filter,
             Control != "Yes",
             Location %in% input$location_select,
             Dilution.factor %in% input$dilution_select) %>%
      group_by(Sample_ID) %>%
      arrange(desc(Freezing.temperature)) %>%
      slice((ceiling(n()*0.05)+1):(floor(n()*0.95))) %>%
      ungroup()
  }), 800)
  curves_trigger <- eventReactive(input$update_plot,
                                  filtered_curves(),
                                  ignoreNULL = FALSE)
  output$curvePlot <- renderPlotly({
    df <- curves_trigger(); req(nrow(df) > 0)
    # 1) Calculate exponentianal between min ane max nm
    exp_range <- floor(log10(min(df$nm))) : ceiling(log10(max(df$nm)))
    # 2) break (10^exp)
    bks  <- 10^exp_range
    # 3) label “10^x”
    labs <- paste0("10^", exp_range)
    # 2) Controllo di quante località e quante size ho nel df attuale
    locs  <- unique(df$Location)
    sizes <- unique(df$Size)
    
    # 3) Define aesthetics conditionally
    if (length(locs) == 1 && length(sizes) > 1) {
      p <- ggplot(df,
                  aes(x = Freezing.temperature,
                      y = nm,
                      text = paste0(
                        "Size: ", Size,
                        "<br>Location: ", Location,
                        "<br>Dilution: ", Dilution.factor,
                        "<br>nm: ", nm,
                        "<br>Temp: ", Freezing.temperature,
                        "<br>Sample: ", Sample
                      ))) +
        geom_point(aes(fill = Size, 
                       shape = Location),
                   size = 2.5,
                   stroke = input$stroke_width,
                   color = "black",
                   alpha = 0.8) +
        scale_shape_manual(values = selected_shapes()) +
        scale_fill_viridis_d(name   = "Size",
                             option = input$palette_select) +
        scale_y_log10(
          breaks = bks,
          labels = labs
        ) +
        scale_x_continuous(breaks = seq(-35, 0, 5)) +
        labs(title    = input$curve_title,
             subtitle = input$curve_subtitle,
             x        = "Temperature (°C)",
             y        = "nm (g⁻¹)") +
        theme_bw() +
        theme(panel.grid.major = element_line(color = "grey80", size = 0.2),
              panel.grid.minor = element_line(color = "grey90", size = 0.1))
      
    } else {
      # Map color on location
      p <- ggplot(df,
                  aes(x = Freezing.temperature,
                      y = nm,
                      text = paste0(
                        "Size: ", Size,
                        "<br>Location: ", Location,
                        "<br>Dilution: ", Dilution.factor,
                        "<br>nm: ", nm,
                        "<br>Temp: ", Freezing.temperature,
                        "<br>Sample: ", Sample
                      ))) +
        geom_point(aes(fill = Location,
                       shape = Location),
                   size = 2.5,
                   stroke = input$stroke_width,
                   color = "black",
                   alpha = 0.8) +
        scale_shape_manual(values = selected_shapes()) +
        scale_fill_viridis_d(name   = "Location",
                             option = input$palette_select) +
        scale_y_log10(
          breaks = bks,
          labels = labs
        ) +
        scale_x_continuous(breaks = seq(-35, 0, 5)) +
        labs(title    = input$curve_title,
             subtitle = input$curve_subtitle,
             x        = "Temperature (°C)",
             y        = "nm (g⁻¹)") +
        theme_bw() +
        theme(panel.grid.major = element_line(color = "grey80", size = 0.2),
              panel.grid.minor = element_line(color = "grey90", size = 0.1))
    }
    
    # 4) Convert to plotly 
    ggplotly(p, tooltip = "text") %>%
      toWebGL() %>%
      config(
        toImageButtonOptions = list(
          format   = "svg",
          filename = "freezing_curves",
          height   = 1200,
          width    = 1800,
          scale    = 2
        )
      )
  })
  
  # 3) COMPARE SAMPLE CURVES ------
  # Populate sample selectors dynamically
  observe({
    req(data_raw())
    samples <- sort(unique(as.character(data_raw()$Sample)))
    updateSelectizeInput(session, "selected_samples_cmp",
                         choices = samples,
                         selected = NULL,
                         server = TRUE)
  })
  
  # Dynamic selectors for Compare Samples FC
  output$dilution_selector_cmp <- renderUI({
    req(data_raw())
    selectInput("dilution_select_cmp","Select dilutions:",
                choices = unique(data_raw()$Dilution.factor),
                selected = unique(data_raw()$Dilution.factor),
                multiple = TRUE)
  })
  
  output$shape_selector_cmp <- renderUI({
    req(input$selected_samples_cmp)
    lapply(input$selected_samples_cmp, function(sample) {
      selectInput(paste0("shape_cmp_", sample),
                  paste("Shape for", sample),
                  choices = c(Circle = 21, Square = 22,
                              Triangle = 24, Diamond = 23),
                  selected = 21)
    })
  })
  
  selected_shapes_cmp <- reactive({
    shapes <- sapply(input$selected_samples_cmp, function(sample) {
      input[[paste0("shape_cmp_", sample)]]
    })
    names(shapes) <- input$selected_samples_cmp
    shapes
  })
  
  filtered_curves_cmp <- debounce(reactive({
    selected_samples <- input$selected_samples_cmp
    
    data_raw() %>%
      filter(Sample %in% selected_samples,
             Size %in% input$Size_filter_cmp,
             Control != "Yes",
             Dilution.factor %in% input$dilution_select_cmp) %>%
      group_by(Sample_ID) %>%
      arrange(desc(Freezing.temperature)) %>%
      slice((ceiling(n()*0.05)+1):(floor(n()*0.95))) %>%
      ungroup()
  }), 800)
  
  curves_trigger_cmp <- eventReactive(input$update_plot_cmp,
                                      filtered_curves_cmp(),
                                      ignoreNULL = FALSE)
  
  output$curvePlot_cmp <- renderPlotly({
    df <- curves_trigger_cmp(); req(nrow(df) > 0)
    exp_range <- floor(log10(min(df$nm))) : ceiling(log10(max(df$nm)))
    bks  <- 10^exp_range
    labs <- paste0("10^", exp_range)
    
    num_samples <- length(unique(df$Sample))
    num_sizes   <- length(unique(df$Size))
    
    # CASE 1: only 1 sample selected, but multiple Sizes -> color by Size
    if (num_samples == 1 && num_sizes > 1) {
      p <- ggplot(df,
                  aes(x = Freezing.temperature,
                      y = nm,
                      text = paste0(
                        "Size: ", Size,
                        "<br>Sample: ", Sample,
                        "<br>Dilution: ", Dilution.factor,
                        "<br>nm: ", nm,
                        "<br>Temp: ", Freezing.temperature
                      ))) +
        geom_point(aes(fill = Size, shape = Sample),
                   size = 2.5,
                   stroke = input$stroke_width_cmp,
                   color = "black",
                   alpha = 0.8) +
        scale_shape_manual(values = selected_shapes_cmp()) +
        scale_fill_viridis_d(name = "Size", option = input$palette_select_cmp) +
        scale_y_log10(breaks = bks, labels = labs) +
        scale_x_continuous(breaks = seq(-35, 0, 5)) +
        labs(title = input$curve_title_cmp,
             subtitle = input$curve_subtitle_cmp,
             x = "Temperature (°C)",
             y = "nm (g⁻¹)") +
        theme_bw() +
        theme(panel.grid.major = element_line(color = "grey80", size = 0.2),
              panel.grid.minor = element_line(color = "grey90", size = 0.1))
      
      # CASE 2: multiple samples (1 or more sizes) -> color by Sample
    } else {
      p <- ggplot(df,
                  aes(x = Freezing.temperature,
                      y = nm,
                      text = paste0(
                        "Size: ", Size,
                        "<br>Sample: ", Sample,
                        "<br>Dilution: ", Dilution.factor,
                        "<br>nm: ", nm,
                        "<br>Temp: ", Freezing.temperature
                      ))) +
        geom_point(aes(fill = Sample, shape = Sample),
                   size = 2.5,
                   stroke = input$stroke_width_cmp,
                   color = "black",
                   alpha = 0.8) +
        scale_shape_manual(values = selected_shapes_cmp()) +
        scale_fill_viridis_d(name = "Sample", option = input$palette_select_cmp) +
        scale_y_log10(breaks = bks, labels = labs) +
        scale_x_continuous(breaks = seq(-35, 0, 5)) +
        labs(title = input$curve_title_cmp,
             subtitle = input$curve_subtitle_cmp,
             x = "Temperature (°C)",
             y = "nm (g⁻¹)") +
        theme_bw() +
        theme(panel.grid.major = element_line(color = "grey80", size = 0.2),
              panel.grid.minor = element_line(color = "grey90", size = 0.1))
    }
    
    ggplotly(p, tooltip = "text") %>%
      toWebGL() %>%
      config(
        toImageButtonOptions = list(
          format   = "svg",
          filename = "compare_samples_curves",
          height   = 1200,
          width    = 1800,
          scale    = 2
        )
      )
  })
  
  # ——————————————
  # 4) TAXA COMPARISON —-----
  # ——————————————
  
  # Reactive for ASV + preprocessing
  processed_data <- reactive({
    req(input$asv_file, metadata_raw())
    
    asv_raw <- read.csv(input$asv_file$datapath, check.names = FALSE)
    
    taxonomy_cols <- c("Kingdom", "Phylum", "Class", "Order", "Family", "Genus", "Species")
    sequence_col <- "seq"
    
    tax_df <- asv_raw[, c(taxonomy_cols, sequence_col)]
    asv_counts <- asv_raw[, !(names(asv_raw) %in% c("Unnamed: 0", taxonomy_cols, sequence_col))]
    
    match_df <- metadata_raw() %>% select(Sequencing_ID, Sample)
    valid_cols <- intersect(colnames(asv_counts), match_df$Sequencing_ID)
    asv_counts <- asv_counts[, valid_cols, drop = FALSE]
    mapped_names <- match_df$Sample[match(valid_cols, match_df$Sequencing_ID)]
    colnames(asv_counts) <- mapped_names
    asv_counts <- asv_counts[, !is.na(colnames(asv_counts)), drop = FALSE]
    
    list(
      tax_df = tax_df,
      asv_counts = asv_counts,
      taxonomy_cols = taxonomy_cols,
      metadata = metadata_raw()
    )
  })
  output$sample_selectors <- renderUI({
    req(processed_data())
    dat <- processed_data()
    if (input$analysis_type == "Per Sample") {
      samples <- colnames(dat$asv_counts)
      tagList(
        selectInput("sample1", "Select Sample 1", choices = samples),
        selectInput("sample2", "Select Sample 2", choices = samples, selected = samples[2])
      )
    } else {
      locs <- unique(dat$metadata$Location)
      tagList(
        selectInput("location1", "Select Location 1", choices = locs),
        selectInput("location2", "Select Location 2", choices = locs, selected = locs[2])
      )
    }
  })
  
  selected_groups <- reactive({
    dat <- processed_data()
    valid_samples <- colnames(dat$asv_counts)
    
    if (input$analysis_type == "Per Sample") {
      list(
        group1 = input$sample1,
        group2 = input$sample2
      )
    } else {
      list(
        group1 = dat$metadata %>%
          filter(Location == input$location1, Sample %in% valid_samples) %>%
          pull(Sample),
        group2 = dat$metadata %>%
          filter(Location == input$location2, Sample %in% valid_samples) %>%
          pull(Sample)
      )
    }
  })
  
  # Selector for samples
  output$title_sample1 <- renderUI({
    if (input$analysis_type == "Per Sample") {
      req(input$sample1)
      h5(paste("Sample:", input$sample1))
    } else {
      req(input$location1)
      h5(paste("Location:", input$location1))
    }
  })
  
  output$title_sample2 <- renderUI({
    if (input$analysis_type == "Per Sample") {
      req(input$sample2)
      h5(paste("Sample:", input$sample2))
    } else {
      req(input$location2)
      h5(paste("Location:", input$location2))
    }
  })
  
  get_aggregated_abundance <- function(sample_cols) {
    dat <- processed_data()
    lvl <- input$tax_level
    asv_mat <- dat$asv_counts[, sample_cols, drop = FALSE]
    if (lvl == "Species") {
      genus <- dat$tax_df[["Genus"]]
      species <- dat$tax_df[["Species"]]
      
      tax_level_vec <- ifelse(
        !is.na(genus) & genus != "" & !is.na(species) & species != "",
        paste(genus, species),
        species  
      )
    } else {
      tax_level_vec <- dat$tax_df[[lvl]]
    }
    
    if (input$analysis_type == "Per Sample") {
      df <- tibble(
        Taxon = tax_level_vec,
        Abundance = asv_mat[[1]]
      ) %>%
        filter(!is.na(Taxon), Taxon != "") %>%
        group_by(Taxon) %>%
        summarise(
          Abundance = sum(Abundance, na.rm = TRUE),
          .groups = "drop"
        ) %>%
        arrange(desc(Abundance)) %>%
        slice_head(n = 30)
    } else {
      summed <- rowSums(asv_mat, na.rm = TRUE)
      df <- tibble(
        Taxon = tax_level_vec,
        Abundance = summed
      ) %>%
        filter(!is.na(Taxon), Taxon != "") %>%
        group_by(Taxon) %>%
        summarise(
          Abundance = sum(Abundance, na.rm = TRUE),
          .groups = "drop"
        ) %>%
        mutate(
          RelAbundance = Abundance / sum(Abundance)
        ) %>%
        mutate(
          Abundance = round(Abundance, 5),
          RelAbundance = round(RelAbundance, 5)
        ) %>%
        arrange(desc(RelAbundance)) %>%
        slice_head(n = 30)
    }
    
    df
  }
  
  output$top_taxa_1 <- renderDT({
    req(selected_groups()$group1)
    datatable(
      get_aggregated_abundance(selected_groups()$group1),
      options = list(
        pageLength = if (input$analysis_type == "Per Sample") 30 else 60
      )
    )
  })
  
  output$top_taxa_2 <- renderDT({
    req(selected_groups()$group2)
    datatable(
      get_aggregated_abundance(selected_groups()$group2),
      options = list(
        pageLength = if (input$analysis_type == "Per Sample") 30 else 60
      )
    )
  })
  
  output$presence_absence_table <- renderDT({
    dat <- processed_data()
    lvl <- input$tax_level
    g1  <- selected_groups()$group1
    g2  <- selected_groups()$group2
    name1 <- if (input$analysis_type == "Per Sample") input$sample1 else input$location1
    name2 <- if (input$analysis_type == "Per Sample") input$sample2 else input$location2
    
    asv_mat <- dat$asv_counts
    tax_vec <- dat$tax_df[[lvl]]
    
    # Calcola la somma per ciascun gruppo
    abund_df <- tibble(
      Taxon = tax_vec,
      Group1 = rowSums(asv_mat[, g1, drop = FALSE], na.rm = TRUE),
      Group2 = rowSums(asv_mat[, g2, drop = FALSE], na.rm = TRUE)
    ) %>%
      filter(!is.na(Taxon), Taxon != "") %>%
      group_by(Taxon) %>%
      summarise(
        Value1 = sum(Group1),
        Value2 = sum(Group2),
        .groups = "drop"
      )
    
    if (input$analysis_type == "Per Location") {
      total1 <- sum(abund_df$Value1)
      total2 <- sum(abund_df$Value2)
      abund_df <- abund_df %>%
        mutate(
          Value1 = round(Value1 / total1, 5),
          Value2 = round(Value2 / total2, 5)
        )
    } else {
      abund_df <- abund_df %>%
        mutate(
          Value1 = round(Value1, 5),
          Value2 = round(Value2, 5)
        )
    }
    
    # Filter: absent if < 0.0001
    abund_df <- abund_df %>%
      mutate(
        Value1 = ifelse(Value1 < 0.0001, 0, Value1),
        Value2 = ifelse(Value2 < 0.0001, 0, Value2)
      )
    
    abund_df <- abund_df %>%
      mutate(
        Status = case_when(
          Value1 > 0 & Value2 > 0 ~ "Present in both",
          Value1 > 0 & Value2 == 0 ~ paste("Only in", name1),
          Value1 == 0 & Value2 > 0 ~ paste("Only in", name2),
          TRUE ~ "Absent in both"
        )
      ) %>%
      filter(Status != "Absent in both") %>%
      mutate(Status = factor(Status, levels = c("Present in both", 
                                                paste("Only in", name1),
                                                paste("Only in", name2)))) %>%
      arrange(Status) %>%
      rename(!!name1 := Value1, !!name2 := Value2)
    
    datatable(abund_df, options = list(pageLength = 50))
  })
  
  output$presence_barplot <- renderPlot({
    req(selected_groups()$group1, selected_groups()$group2)
    
    dat <- processed_data()
    lvl <- input$tax_level
    g1 <- selected_groups()$group1
    g2 <- selected_groups()$group2
    
    name1 <- if (input$analysis_type == "Per Sample") input$sample1 else input$location1
    name2 <- if (input$analysis_type == "Per Sample") input$sample2 else input$location2
    
    df <- dat$asv_counts %>%
      mutate(Taxon = dat$tax_df[[lvl]]) %>%
      filter(!is.na(Taxon), Taxon != "") %>%
      group_by(Taxon) %>%
      summarise(
        Present1 = any(across(all_of(g1)) > 0),
        Present2 = any(across(all_of(g2)) > 0),
        .groups = "drop"
      )
    
    n1  <- sum(df$Present1 & !df$Present2)
    n2  <- sum(df$Present2 & !df$Present1)
    n12 <- sum(df$Present1 & df$Present2)
    
    labels <- c(paste("Only in", name1), paste("Only in", name2), "Shared")
    plot_df <- tibble(
      Category = factor(labels, levels = labels),
      Count = c(n1, n2, n12)
    )
    
    color_map <- setNames(
      c("#2980b9", "#7f8c8d", "#16a085"),
      labels
    )
    
    ggplot(plot_df, aes(x = Category, y = Count, fill = Category)) +
      geom_col(width = 0.6) +
      geom_text(aes(label = Count), hjust = -0.1, size = 5, color = "black", fontface = "bold") +
      coord_flip() +
      scale_fill_manual(values = color_map) +
      labs(title = "Unique and Shared Taxa", x = "", y = "Number of Taxa") +
      theme_minimal(base_size = 14) +
      theme(
        legend.position = "none",
        plot.title = element_text(hjust = 0.5, face = "bold"),
        axis.text.y = element_text(face = "bold"),
        axis.text.x = element_text(color = "gray30"),
        panel.grid.major.y = element_blank()
      ) +
      expand_limits(y = max(plot_df$Count) * 1.1)
  })
  
  ## 5) FROZEN FRACTION ------ 
  observe({
    updateSelectInput(session, "Size_filter_ff",
                      choices = unique(data_raw()$Size),
                      selected = unique(data_raw()$Size))
  })
  output$sample_selector <- renderUI({ req(data_raw())
    selectInput("sample_select", "Select sample:",
                choices = unique(data_raw()$Sample),
                selected = unique(data_raw()$Sample)[1])
  })
  output$shape_selector_ff <- renderUI({
    tagList(
      selectInput("shape_sample", "Shape for sample:",
                  choices = c(Circle=21,Square=22,
                              Triangle=24,Diamond=23),
                  selected = 21),
      selectInput("shape_control", "Shape for control:",
                  choices = c(Circle=21,Square=22,
                              Triangle=24,Diamond=23),
                  selected = 22)
    )
  })
  shapes_ff <- reactive(c(No = as.numeric(input$shape_sample),
                          Yes = as.numeric(input$shape_control)))
  ff_data <- eventReactive(input$update_ff, {
    data_raw() %>%
      filter(Sample == input$sample_select,
             Size %in% input$Size_filter_ff) %>%
      mutate(FillColor = ifelse(tolower(Control) == "no",
                                as.character(Dilution.factor), NA))
  }, ignoreNULL = FALSE)
  output$ffPlot <- renderPlotly({
    df2 <- ff_data(); req(nrow(df2) > 0)
    df2 <- df2 %>% filter(!is.na(FF), !is.na(Freezing.temperature))
    p2 <- ggplot(df2,
                 aes(x = Freezing.temperature,
                     y = FF,
                     color = Dilution.factor,
                     shape = Control,
                     fill = FillColor)) +
      geom_point(size = 2.5, alpha = 1, stroke = 0.3) +
      scale_shape_manual(values = shapes_ff()) +
      scale_fill_viridis_d(na.value = "white") +
      scale_color_viridis_d(option = input$palette_select_ff) +
      facet_wrap(~Size, scales = "free") +
      labs(title = input$ff_title,
           subtitle = input$ff_subtitle,
           x = "Temperature (°C)",
           y = "Frozen fraction",
           color = "Dilution.factor",
           fill = "Dilution.factor",
           shape = "Control") +
      theme_bw() +
      theme(panel.grid = element_blank(),
            panel.border = element_rect(color = "black"),
            axis.text.x = element_text(angle = 45, hjust = 1))
    if(!input$show_control) p2 <- p2 + guides(shape = "none", fill = "none")
    ggplotly(p2, tooltip = c("x", "y")) %>%
      config(
        toImageButtonOptions = list(
          format = "svg",
          filename = "frozen_fraction",
          height = 1200,
          width = 1800,
          scale = 2
        )
      )
  })
  
  # Download handler first 2
  output$downloadPlot <- downloadHandler(
    filename = function() {
      if(input$vis_type == "Freezing Curves") "freezing_curves.png"
      else "frozen_fraction.png"
    },
    content = function(file) {
      if(input$vis_type == "Freezing Curves") {
        png(file, width = 10, height = 6, units = "in", res = 300)
        print(ggplot(curves_trigger(),
                     aes(x = Freezing.temperature, y = nm)) +
                geom_point())
        dev.off()
      } else {
        png(file, width = 10, height = 6, units = "in", res = 300)
        print(ggplot(ff_data(),
                     aes(x = Freezing.temperature, y = FF)) +
                geom_point())
        dev.off()
      }
    }
  )
  
  # —————————
  # 6) KNEEPOINT ANALYSIS --------------
  # —————————
  df_all_pw <- reactive({ data_raw() })
  
  # 6.1: dynamic selectors
  output$sample_ui_pw <- renderUI({
    req(df_all_pw())
    selectInput("sample_pw", "Sample:",
                unique(df_all_pw()$Sample))
  })
  output$Size_ui_pw <- renderUI({
    req(df_all_pw())
    selectInput("Size_pw", "Size:",
                unique(df_all_pw()$Size))
  })
  output$dilution_ui_pw <- renderUI({
    req(df_all_pw())
    selectInput("dilution_pw", "Dilutions:",
                choices = unique(df_all_pw()$Dilution.factor),
                selected = unique(df_all_pw()$Dilution.factor),
                multiple = TRUE)
  })
  
  # 6.2: filtering and preparation
  curve_data_pw <- eventReactive(input$go_pw, {
    req(input$sample_pw, input$Size_pw, input$dilution_pw)
    df <- df_all_pw() %>%
      filter(Sample == input$sample_pw,
             Size == input$Size_pw,
             Control != "Yes",
             Dilution.factor %in% input$dilution_pw) %>%
      mutate(log_nm = log10(nm)) %>%
      filter(!is.na(nm), nm > 0) %>%
      arrange(Freezing.temperature) %>%
      dplyr::select(Freezing.temperature, log_nm)
  })
  
  ## —————————
  ## 6.3: spline + piecewise (reactive to go_pw + spar_pw)
  ## —————————
  
  # 1) trigger that starts after “Run Analysis”
  go_flag <- eventReactive(input$go_pw, {
    Sys.time()
  })
  
  # 2) reactive dependent on go_flag() and input$spar_pw
  pw_fit_pw <- reactive({
    # waits for first click
    req(go_flag())
    # then validates every time that spar_pw change
    input$spar_pw
    
    # isolate the heavy calculation from other inputs
    isolate({
      cd <- curve_data_pw()
      x  <- cd$Freezing.temperature
      y  <- cd$log_nm
      
      if (length(x) < 4 || any(!is.finite(x)) || any(!is.finite(y))) {
        showNotification("Error: insufficient or unvalid data.", type = "error")
        return(NULL)
      }
      
      # 6.3.1 spline with adjustable spar
      sp   <- smooth.spline(x, y, spar = input$spar_pw)
      xx   <- seq(min(x), max(x), length.out = 2000)
      pred <- suppressWarnings(stats::predict(sp, xx, se = TRUE))
      se_vec <- if(!is.null(pred$se)) pred$se else rep(NA_real_, length(xx))
      df_sp <- data.frame(x = xx, y = pred$y, se = se_vec)
      
      # 6.3.2 piecewise
      lm0 <- lm(y ~ x, data = df_sp)
      seg <- segmented(
        lm0,
        seg.Z   = ~x,
        npsi    = input$n_breaks_pw,
        control = seg.control(it.max = 50, display = FALSE)
      )
      
      # 6.3.3 ordering from high T to low T
      bps_raw <- seg$psi[, "Est."]
      ord     <- order(bps_raw, decreasing = TRUE)
      bps     <- bps_raw[ord]
      
      # 6.3.4 calculate nm to break point 
      idx_raw <- sapply(bps_raw, function(b) which.min(abs(df_sp$x - b)))
      nm_raw  <- 10^(df_sp$y[idx_raw])
      nm_vals <- nm_raw[ord]
      
      df_sp$yhat <- predict(seg)
      
      # 6.3.5 print list
      list(
        raw     = cd,
        spline  = df_sp,
        bps     = bps,
        nm_vals = nm_vals,
        seg     = seg
      )
    })
  })
  #Statistics
  heavy_stats <- eventReactive(input$run_stats, {
    pf  <- pw_fit_pw()
    req(pf)
    cd   <- pf$raw
    df_sp<- pf$spline
    seg  <- pf$seg
    
    # 1) Parametric CI
    ci_mat <- confint(seg)[grep("^psi", rownames(confint(seg))), , drop = FALSE]
    
    # 2) ANOVA
    lm_plain  <- lm(y ~ x, data = df_sp)
    anova_res <- anova(lm_plain, seg)
    
    # 3) NON-PARAMETRIC BOOTSTRAP ON ALL BREAKPOINT
    npsi     <- length(pf$bps)
    boot_stat <- function(data, i) {
      d <- data[i, ]
      m0 <- lm(log_nm ~ Freezing.temperature, data = d)
      sg <- try(segmented(m0, seg.Z = ~Freezing.temperature, npsi = npsi), silent = TRUE)
      if (inherits(sg, "try-error")) return(rep(NA_real_, npsi))
      sg$psi[, "Est."]
    }
    set.seed(123)
    bres     <- boot(data = cd, statistic = boot_stat, R = 500)
    
    # extract matrice R x npsi and remove evetual lines with NA
    t_mat    <- bres$t
    t_mat    <- t_mat[complete.cases(t_mat), , drop = FALSE]
    
    # Calculate percentile 2.5% and 97.5% for each breakpoint
    ci_boot_mat <- t(apply(t_mat, 2, quantile, probs = c(0.025, 0.975), na.rm = TRUE))
    colnames(ci_boot_mat) <- c("CI(2.5%)", "CI(97.5%)")
    
    # ordering resukts bootstrap based on pf$bps (decreasing)
    ord   <- order(pf$bps, decreasing = TRUE)
    ci_boot_mat <- ci_boot_mat[ord, , drop = FALSE]
    
    # 4) Residues & fitted
    resid_vals  <- resid(seg)
    fitted_vals <- fitted(seg)
    
    # 5) Cross Validation 5-FOLD on each breakpoint
    folds <- createFolds(cd$log_nm, k = 5)
    cv_raw <- sapply(folds, function(idx) {
      dtr <- cd[-idx, ]
      m0  <- lm(log_nm ~ Freezing.temperature, data = dtr)
      sg  <- try(segmented(m0, seg.Z = ~Freezing.temperature, npsi = npsi), silent = TRUE)
      if (inherits(sg, "try-error")) return(rep(NA_real_, npsi))
      sg$psi[, "Est."]
    })
    
    # If the breakpoint is one, sapply gives a vector:
    if (npsi == 1 && is.null(dim(cv_raw))) {
      cv_mat <- matrix(cv_raw, nrow = 1)
    } else {
      cv_mat <- cv_raw
    }
    
    cv_mean <- rowMeans(cv_mat, na.rm = TRUE)
    cv_sd   <- apply(cv_mat, 1, sd, na.rm = TRUE)
    
    # order mean and cv
    cv_mean <- cv_mean[ord]
    cv_sd   <- cv_sd[ord]
    
    list(
      ci_mat    = ci_mat,
      anova_res = anova_res,
      ci_boot   = ci_boot_mat,
      resid     = resid_vals,
      fitted    = fitted_vals,
      cv_mean   = cv_mean,
      cv_sd     = cv_sd
    )
  })
  
  output$download_heavy_stats <- downloadHandler(
    filename = function() {
      paste0("kneepoint_statistics_", Sys.Date(), ".zip")
    },
    content = function(zipfile) {
      stats <- heavy_stats()
      req(stats)
      
      # Create temp files for each table
      tmpdir <- tempdir()
      ci_file      <- file.path(tmpdir, "parametric_ci.csv")
      anova_file   <- file.path(tmpdir, "anova_vs_linear.csv")
      bootstrap_file <- file.path(tmpdir, "bootstrap_ci.csv")
      cv_file      <- file.path(tmpdir, "cross_validation.csv")
      
      # Save each table
      write.csv(stats$ci_mat, ci_file, row.names = FALSE)
      write.csv(stats$anova_res, anova_file, row.names = FALSE)
      write.csv(stats$ci_boot, bootstrap_file, row.names = FALSE)
      
      cv_df <- data.frame(
        Breakpoint = seq_along(stats$cv_mean),
        Mean       = sprintf("%.2f", stats$cv_mean),
        SD         = sprintf("%.2f", stats$cv_sd)
      )
      write.csv(cv_df, cv_file, row.names = FALSE)
      
      # Zip all
      zip::zipr(
        zipfile = zipfile,
        files = c(ci_file, anova_file, bootstrap_file, cv_file),
        root = tmpdir
      )
    }
  )
  
  # Print KP values with CI
  output$kp_values_pw <- renderPrint({
    pf <- pw_fit_pw(); if(is.null(pf)) return()
    ord     <- order(pf$bps, decreasing = TRUE)
    psi_ord <- pf$bps[ord]
    nm_ord  <- pf$nm_vals[ord]
    
    msgs <- character(length(psi_ord))
    for(i in seq_along(psi_ord)) {
      msgs[i] <- sprintf("KP%d: %.2f°C, nm=%.3g", i, psi_ord[i], nm_ord[i])
    }
    cat(paste(msgs, collapse = "\n"))
  })
  
  # Plot piecewise + CI + KP
  output$plot_piecewise_pw <- renderPlotly({
    pal_spline <- if(is.null(input$ci_spline_palette)) "Set2" else input$ci_spline_palette
    pal_piece  <- if(is.null(input$palette_pw))      "Set1" else input$palette_pw
    spline_cols <- brewer.pal(3, pal_spline)
    piece_cols  <- brewer.pal(3, pal_piece)
    pf <- pw_fit_pw(); if(is.null(pf)) return(NULL)
    
    all_y <- c(pf$raw$log_nm, pf$spline$y, pf$spline$yhat)
    exp_range <- floor(min(all_y)) : ceiling(max(all_y))
    labs_y    <- paste0("10^", exp_range)
    
    p <- ggplot() + theme_minimal() +
      labs(title = input$plot_title_pw,
           subtitle = input$plot_subtitle_pw,
           x = "Temperature (°C)", y = "log10(nm)") +
      
      scale_y_continuous(
        breaks = exp_range,
        labels = labs_y
      )
    
    if("Curve" %in% input$display_layers_pw)
      p <- p + geom_point(data = pf$raw,
                          aes(Freezing.temperature, log_nm),
                          color="#444444", alpha=0.3)
    if("spline" %in% input$display_layers_pw)
      p <- p + geom_line(data = pf$spline,
                         aes(x, y), color=piece_cols[2], linewidth=1)
    if("spline CI" %in% input$display_layers_pw)
      p <- p + geom_ribbon(data = pf$spline,
                           aes(x = x,
                               ymin = y - 1.96 * se,
                               ymax = y + 1.96 * se),
                           fill = spline_cols[2],
                           alpha = 0.2)
    
    if("Piecewise" %in% input$display_layers_pw) {
      p <- p + geom_line(data = pf$spline,
                         aes(x, yhat),
                         color = piece_cols[3],
                         linewidth = 1.2)
      
      # replace loop + geom_segment with annotate()
      min_y <- min(pf$spline$y)
      for(i in seq_along(pf$bps)) {
        kp_x <- pf$bps[i]
        kp_y <- pf$spline$y[which.min(abs(pf$spline$x - kp_x))]
        
        p <- p + annotate(
          "segment",
          x    = kp_x, xend = kp_x,
          y    = min_y, yend = kp_y,
          linetype = "dotted",
          color    = piece_cols[3],
          size     = 0.5
        )
      }
    }
    # 6) Print interval, ANOVA, bootstrap and CV
    output$stats_pw <- renderUI({
      stats <- heavy_stats()
      req(stats)
      
      tagList(
        tags$h4("Parametric Confidence Intervals (CI)", style = "margin-top: 20px; font-weight: bold;"),
        renderTable({ stats$ci_mat }, striped = TRUE, bordered = TRUE, spacing = "s"),
        
        tags$h4("ANOVA vs Linear Model", style = "margin-top: 20px; font-weight: bold;"),
        renderTable({ as.data.frame(stats$anova_res) }, striped = TRUE, bordered = TRUE, spacing = "s"),
        
        tags$h4("Bootstrap CI for Breakpoints (Percentile)", style = "margin-top: 20px; font-weight: bold;"),
        renderTable({ as.data.frame(stats$ci_boot) }, striped = TRUE, bordered = TRUE, spacing = "s"),
        
        tags$h4("5-Fold Cross-Validation", style = "margin-top: 20px; font-weight: bold;"),
        renderTable({
          data.frame(
            Breakpoint = seq_along(stats$cv_mean),
            Mean       = sprintf("%.2f°C", stats$cv_mean),
            SD         = sprintf("%.2f°C", stats$cv_sd)
          )
        }, striped = TRUE, bordered = TRUE, spacing = "s")
      )
    })
    
    # 7) Diagnostic plots / QQ-plot
    output$resid_diag_pw <- renderPlot({
      stats <- heavy_stats()
      req(stats)
      par(mfrow = c(1,2))
      plot(stats$fitted, stats$resid,
           main = "Residues vs Fitted",
           xlab = "Fitted", ylab = "Residues")
      abline(h = 0, lty = 2)
      qqnorm(stats$resid, main = "QQ-Plot residues")
      qqline(stats$resid)
    })
    output$ci_table <- renderTable({
      stats <- heavy_stats()
      req(stats)
      stats$ci_mat
    }, striped = TRUE, bordered = TRUE, spacing = "s")
    
    output$anova_table <- renderTable({
      stats <- heavy_stats()
      req(stats)
      as.data.frame(stats$anova_res)
    }, striped = TRUE, bordered = TRUE, spacing = "s")
    
    output$bootstrap_table <- renderTable({
      stats <- heavy_stats()
      req(stats)
      as.data.frame(stats$ci_boot)
    }, striped = TRUE, bordered = TRUE, spacing = "s")
    
    output$cv_table <- renderTable({
      stats <- heavy_stats()
      req(stats)
      data.frame(
        Breakpoint = seq_along(stats$cv_mean),
        Mean       = sprintf("%.2f°C", stats$cv_mean),
        SD         = sprintf("%.2f°C", stats$cv_sd)
      )
    }, striped = TRUE, bordered = TRUE, spacing = "s")
    if("Show KP" %in% input$display_layers_pw) {
      min_y <- min(pf$spline$y)
      for(i in seq_along(pf$bps)) {
        kp_x <- pf$bps[i]
        kp_y <- pf$spline$y[which.min(abs(pf$spline$x - kp_x))]
        
        p <- p + annotate(
          "segment",
          x    = kp_x, xend = kp_x,
          y    = min_y, yend = kp_y,
          linetype = "dotted",
          color    = piece_cols[1],
          size     = 0.5
        )
      }
    }
    
    ggplotly(p) %>%
      config(
        dragmode = "zoom",
        toImageButtonOptions = list(
          format = "svg",
          filename = "kneepoint_curve",
          height = 1200,
          width = 1800,
          scale = 2
        )
      )
  })
  # —————————————————————————
  # 7) BOXPLOT COMPARISON SERVER LOGIC-------
  # —————————————————————————
  
  # 7.1) Dynamically populate the "comparison_column_ui" dropdown,
  #      excluding "nM_10" and "nM_15" from the choices
  output$comparison_column_ui <- renderUI({
    req(nm_metadata())
    all_cols <- names(nm_metadata())
    choices <- setdiff(all_cols, c("nM10_b5", "nM15_b5", "nM10_b02", "nM15_b02", "GenLatitude", "Latitude", "Longitude"))
    selectInput("comparison_column", "Select variable to compare:", choices = choices)
  })
  
  output$binning_options <- renderUI({
    req(input$comparison_column)
    # check if column is numeric
    df <- metadata_with_nm()
    if ( is.numeric(df[[ input$comparison_column ]]) ) {
      tagList(
        radioButtons("bin_method",
                     "Binning method:",
                     choices = c(
                       "Quartiles (4 equal groups)"      = "quartiles",
                       "Custom number of bins"           = "n_bins",
                       "Custom breaks"                   = "custom"
                     ),
                     selected = "quartiles"),
        conditionalPanel(
          "input.bin_method == 'n_bins'",
          numericInput("n_bins", "Number of bins:", value = 4, min = 2, max = 10, step = 1)
        ),
        conditionalPanel(
          "input.bin_method == 'custom'",
          textInput("custom_breaks",
                    "Breaks (comma-separated)",
                    placeholder = "es: 0,5,7,10,15")
        )
      )
    }
  })
  
  # 7.2) Reactive for Boxplot e Statistics: automatic grouping
  boxplot_df <- reactive({
    req(input$nm_choice, input$size_choice, input$comparison_column)
    df <- metadata_with_nm()
    
    # 1) Select column nM
    nm_col <- switch(
      paste(input$size_choice, input$nm_choice, sep = "_"),
      "b_5_m_nM_10"  = "nM10_b5",
      "b_5_m_nM_15"  = "nM15_b5",
      "b_02_m_nM_10" = "nM10_b02",
      "b_02_m_nM_15" = "nM15_b02"
    )
    
    # 2) If numeric calculte breakpoint 
    if ( is.numeric(df[[input$comparison_column]]) ) {
      vec <- df[[ input$comparison_column ]]
      breaks <-
        switch(input$bin_method,
               quartiles = quantile(vec, probs = seq(0, 1, length.out = 5), na.rm = TRUE),
               n_bins    = seq(min(vec, na.rm=TRUE),
                               max(vec, na.rm=TRUE),
                               length.out = input$n_bins + 1),
               custom    = as.numeric(strsplit(input$custom_breaks, ",")[[1]])
        )
      df <- df %>%
        mutate(comparison = cut(vec,
                                breaks = breaks,
                                include.lowest = TRUE,
                                dig.lab = 10))
    } else {
      df <- df %>% mutate(comparison = as.factor(.data[[input$comparison_column]]))
    }
    
    # 3) finale
    df %>%
      filter(!is.na(comparison), !is.na(.data[[nm_col]])) %>%
      transmute(
        Sample     = Sample,
        comparison = comparison,
        nm_value   = .data[[ nm_col ]]
      )
  })
  
  output$binning_info <- renderTable({
    df <- boxplot_df()
    if (!"comparison" %in% names(df)) return(NULL)
    tibble(
      Category = levels(df$comparison),
      Range     = levels(df$comparison)
    )
  })
  
  # 7.3) RenderPlotly: create a boxplot of (nM_10 or nM_15) vs. the chosen grouping column.
  output$boxplot_comparison <- renderPlotly({
    df_summary <- boxplot_df()
    req(nrow(df_summary) > 0)
    
    # Built boxplot, on 'comparison'
    p_box <- ggplot(df_summary,
                    aes(x = comparison,
                        y = nm_value,
                        fill = comparison,
                        text = Sample)) +
      geom_boxplot() +
      geom_jitter(width = 0.2, alpha = 0.5) +
      {
        n_groups <- length(unique(df_summary$comparison))
        selected_palette <- input$boxplot_palette
        
        if (selected_palette == "viridis") {
          scale_fill_viridis_d(option = "D")  # puoi cambiarlo in plasma/magma etc.
        } else if (selected_palette %in% rownames(RColorBrewer::brewer.pal.info)) {
          max_colors <- RColorBrewer::brewer.pal.info[selected_palette, "maxcolors"]
          if (n_groups <= max_colors) {
            scale_fill_brewer(palette = selected_palette)
          } else {
            scale_fill_manual(values = viridis::viridis(n_groups))
          }
        } else {
          scale_fill_brewer(palette = "Set3")
        }
      } +
      scale_y_log10(
        breaks = scales::log_breaks(base = 10),
        labels = function(x) paste0("10^", log10(x))
      ) +
      labs(
        title = paste0("Distribution of ", input$nm_choice,
                       " per ", input$comparison_column,
                       if (is.numeric(metadata_with_nm()[[input$comparison_column]]))
                         " (grouped by Location)" else ""),
        x = if (is.numeric(metadata_with_nm()[[input$comparison_column]]))
          "Location" else input$comparison_column,
        y = input$nm_choice
      ) +
      theme_bw() +
      theme(
        plot.title  = element_text(size = 14, face = "bold"),
        axis.text.x = element_text(angle = 45, hjust = 1)
      )
    
    ggplotly(p_box, tooltip = c("x", "y", "text")) %>%
      config(toImageButtonOptions = list(
        format   = "svg",
        filename = paste0(
          "boxplot_",
          input$nm_choice,
          "_by_",
          if (is.numeric(metadata_with_nm()[[input$comparison_column]]))
            "Location" else input$comparison_column
        ),
        height   = 1000,
        width    = 1600,
        scale    = 2
      ))
  })
  
  # 7.4) Boxplot Statistics ------
  boxplot_stats <- eventReactive(input$run_boxplot_stats, {
    stats_df <- boxplot_df()
    req(nrow(stats_df) > 0)
    
    nm_var    <- "nm_value"
    group_var <- "comparison"
    
    shapiro_res <- shapiro_test(data.frame(var = stats_df[[nm_var]]), var)
    pval <- shapiro_res$p
    
    if (!is.na(pval) && pval > 0.05) {
      aov_res  <- anova_test(stats_df, formula = nm_value ~ comparison)
      posthoc  <- tukey_hsd(stats_df, formula = nm_value ~ comparison)
      effect   <- eta_squared(aov(lm(nm_value ~ comparison, data = stats_df)))
      test_type <- "ANOVA + Tukey HSD"
    } else {
      aov_res  <- kruskal_test(stats_df, formula = nm_value ~ comparison)
      posthoc  <- dunn_test(stats_df, formula = nm_value ~ comparison, p.adjust.method = "bonferroni")
      effect   <- kruskal_effsize(stats_df, formula = nm_value ~ comparison)
      test_type <- "Kruskal-Wallis + Dunn"
    }
    
    list(
      test_type   = test_type,
      aov_res     = aov_res,
      posthoc_res = posthoc,
      effect_size = effect,
      data        = stats_df
    )
  })
  
  output$boxplot_stats <- renderUI({
    stats <- boxplot_stats()
    req(stats)
    
    group_var <- input$comparison_column
    
    tagList(
      tags$h4("Statistical Analysis Type", style = "margin-top: 20px; font-weight: bold;"),
      tags$p(stats$test_type),
      
      tags$h4("Main Test Results", style = "margin-top: 20px; font-weight: bold;"),
      renderTable({ stats$aov_res }, striped = TRUE, bordered = TRUE, spacing = "s"),
      
      tags$h4("Post-hoc Comparisons", style = "margin-top: 20px; font-weight: bold;"),
      DT::renderDataTable({
        posthoc_df <- stats$posthoc_res
        # Check if .y. column exists and remove it
        if (".y." %in% colnames(posthoc_df)) {
          posthoc_df <- posthoc_df %>% select(-.y.)
        }
        # If no rows, return a dummy message table
        if (nrow(posthoc_df) == 0) {
          data.frame(Message = "No pairwise comparisons available")
        } else {
          posthoc_df
        }
      }, options = list(pageLength = 5, autoWidth = TRUE)),
      
      tags$h4("Effect Size", style = "margin-top: 20px; font-weight: bold;"),
      renderTable({ stats$effect_size }, striped = TRUE, bordered = TRUE, spacing = "s"),
    )
  })
  
  output$residual_diag_boxplot <- renderPlot({
    stats <- boxplot_stats()
    req(stats)
    
    lm_model <- lm(as.formula(paste(input$nm_choice, "~", input$comparison_column)), data = stats$data)
    
    par(mfrow = c(1,2))
    plot(lm_model$fitted.values, lm_model$residuals,
         main = "Residuals vs Fitted",
         xlab = "Fitted values", ylab = "Residuals")
    abline(h = 0, lty = 2)
    
    qqnorm(lm_model$residuals, main = "QQ-Plot Residuals")
    qqline(lm_model$residuals)
  })
  
  output$download_stats <- downloadHandler(
    filename = function() {
      paste0("boxplot_statistics_", Sys.Date(), ".zip")
    },
    content = function(zipfile) {
      stats <- boxplot_stats()
      req(stats)
      
      group_var <- input$comparison_column
      
      tmpdir <- tempdir()
      main_test_file <- file.path(tmpdir, "main_test.csv")
      posthoc_file   <- file.path(tmpdir, "posthoc.csv")
      effect_file    <- file.path(tmpdir, "effect_size.csv")
      
      write.csv(stats$aov_res, main_test_file, row.names = FALSE)
      
      posthoc_df <- stats$posthoc_res
      if (".y." %in% colnames(posthoc_df)) {
        posthoc_df <- posthoc_df %>% select(-.y.)
      }
      if (nrow(posthoc_df) == 0) {
        write.csv(data.frame(Message = "No pairwise comparisons available"), posthoc_file, row.names = FALSE)
      } else {
        write.csv(posthoc_df, posthoc_file, row.names = FALSE)
      }
      
      write.csv(stats$effect_size, effect_file, row.names = FALSE)
      
      zip::zipr(zipfile, files = c(main_test_file, posthoc_file, effect_file), root = tmpdir)
    }
  )
  #8) CORRELATION ANALYSIS -----
  nm_metadata <- reactive({
    req(metadata_with_nm())
    
    metadata_with_nm() %>%
      filter(
        !is.na(nM10_b5), !is.na(nM15_b5),
        !is.na(nM10_b02), !is.na(nM15_b02),
        !is.na(GenLatitude)
      )
  })
  
  observe({
    req(nm_metadata())
    metadata_cols <- names(nm_metadata())
    numeric_cols <- metadata_cols[sapply(nm_metadata(), is.numeric)]
    exclude_cols <- c("nM10_b5", "nM10_b02", "nM15_b5", "nM15_b02")
    valid_choices <- setdiff(numeric_cols, exclude_cols)
    
    updateSelectInput(session, "spearman_var",
                      choices = valid_choices,
                      selected = "GenLatitude")
  })
  
  library(mgcv)  # make sure it’s loaded
  
  analysis_results <- eventReactive(input$run_spearman, {
    req(input$analysis_method, input$spearman_var)
    method <- input$analysis_method
    xvar   <- input$spearman_var
    df     <- nm_metadata()
    
    out <- list()
    for(metric in c("nM10", "nM15")) {
      # build the combined data.frame of the two sizes, preserving Location
      dat_comb <- bind_rows(
        df %>%
          transmute(
            X        = !!sym(xvar),
            nM       = !!sym(paste0(metric, "_b5")),
            Size     = "b_5_m",
            Location = Location
          ),
        df %>%
          transmute(
            X        = !!sym(xvar),
            nM       = !!sym(paste0(metric, "_b02")),
            Size     = "b_02_m",
            Location = Location
          )
      ) %>%
        filter(!is.na(X), !is.na(nM))
      
      dat_comb$Size <- factor(dat_comb$Size, levels = c("b_5_m", "b_02_m"))
      
      # compute the subtitle labels
      # build a named vector of labels, one per Size level
      labels <- setNames(
        lapply(c("b_5_m","b_02_m"), function(sz) {
          subdf <- filter(dat_comb, Size == sz)
          if(method %in% c("Spearman","Pearson")) {
            ct <- cor.test(subdf$X, subdf$nM, method = tolower(method))
            sprintf("%s: rho=%.2f, p=%.3g", sz, ct$estimate, ct$p.value)
          } else if(method == "Quadratic Fit") {
            fit   <- lm(nM ~ poly(X,2), data = subdf)
            fstat <- summary(fit)$fstatistic
            pval  <- pf(fstat[1], fstat[2], fstat[3], lower.tail = FALSE)
            r2    <- summary(fit)$r.squared
            sprintf("%s: R²=%.2f, p=%.3g", sz, r2, pval)
          } else if(method == "GAM") {
            fit <- gam(nM ~ s(X), data = subdf)
            sm  <- summary(fit)
            sprintf("%s: R²=%.2f, p=%.3g", sz, sm$r.sq, sm$s.pv[1])
          }
        }),
        c("b_5_m","b_02_m")
      )
      
      out[[metric]] <- list(
        data   = dat_comb,
        labels = labels    # named vector: labels["b_5_m"], labels["b_02_m"]
      )
    }
    out
  })
  
  output$plot_nM10 <- renderPlot({
    res     <- analysis_results(); req(res)
    df      <- res[["nM10"]]$data
    labels  <- res[["nM10"]]$labels
    method  <- input$analysis_method
    
    ggplot(df, aes(x = X, y = nM)) +
      geom_point(aes(color = Location), size = 3) +
      {
        if (method %in% c("Spearman", "Pearson")) {
          geom_smooth(aes(group = 1),
                      method  = "lm",
                      formula = y ~ x,
                      se      = TRUE,
                      color   = "black",
                      fill    = "gray70")
        } else if (method == "Quadratic Fit") {
          geom_smooth(aes(group = 1),
                      method  = "lm",
                      formula = y ~ poly(x, 2),
                      se      = TRUE,
                      color   = "black",
                      fill    = "gray70")
        } else if (method == "GAM") {
          geom_smooth(aes(group = 1),
                      method  = "gam",
                      formula = y ~ s(x),
                      se      = TRUE,
                      color   = "black",
                      fill    = "gray70")
        }
      } +
      facet_wrap(~ Size, scales = "free_y",
                 labeller = labeller(Size = function(x) labels[x])) +
      scale_y_log10(
        breaks = scales::trans_breaks("log10", function(x) 10^x,   n = 5),    # aumenta n se vuoi più tick
        labels = scales::trans_format("log10", scales::math_format(10^.x))
      ) +
      labs(
        title = paste0(method, " of ", input$spearman_var, " vs nM\u2081\u2080"),
        x     = input$spearman_var,
        y     = expression(nM[10])
      ) +
      theme_bw(base_size = 14)
  })
  
  output$plot_nM15 <- renderPlot({
    res     <- analysis_results(); req(res)
    df      <- res[["nM15"]]$data
    labels  <- res[["nM15"]]$labels
    method  <- input$analysis_method
    
    ggplot(df, aes(x = X, y = nM)) +
      geom_point(aes(color = Location), size = 3) +
      {
        if (method %in% c("Spearman", "Pearson")) {
          geom_smooth(aes(group = 1),
                      method  = "lm",
                      formula = y ~ x,
                      se      = TRUE,
                      color   = "black",
                      fill    = "gray70")
        } else if (method == "Quadratic Fit") {
          geom_smooth(aes(group = 1),
                      method  = "lm",
                      formula = y ~ poly(x, 2),
                      se      = TRUE,
                      color   = "black",
                      fill    = "gray70")
        } else if (method == "GAM") {
          geom_smooth(aes(group = 1),
                      method  = "gam",
                      formula = y ~ s(x),
                      se      = TRUE,
                      color   = "black",
                      fill    = "gray70")
        }
      } +
      facet_wrap(~ Size, scales = "free_y",
                 labeller = labeller(Size = function(x) labels[x])) +
      scale_y_log10(
        breaks = scales::trans_breaks("log10", function(x) 10^x,   n = 5),    # increase if you need more ticks
        labels = scales::trans_format("log10", scales::math_format(10^.x))
      ) +
      labs(
        title = paste0(method, " of ", input$spearman_var, " vs nM\u2081\u2085"),
        x     = input$spearman_var,
        y     = expression(nM[15])
      ) +
      theme_bw(base_size = 14)
  })
  
  output$download_spearman <- downloadHandler(
    filename = function() {
      paste0("Correlation_", input$analysis_method, "_",
             input$spearman_var, "_all.png")
    },
    content = function(file) {
      # fetch results
      res    <- analysis_results(); req(res)
      method <- input$analysis_method
      var    <- input$spearman_var
      
      # build the nM10 plot
      df10   <- res[["nM10"]]$data
      labels10 <- res[["nM10"]]$labels
      p10 <- ggplot(df10, aes(x = X, y = nM)) +
        geom_point(aes(color = Location), size = 3) +
        {
          if (method %in% c("Spearman","Pearson"))
            geom_smooth(aes(group = 1), method = "lm",  formula = y ~ x,
                        se = TRUE, color = "black", fill = "gray70")
          else if (method == "Quadratic Fit")
            geom_smooth(aes(group = 1), method = "lm",  formula = y ~ poly(x,2),
                        se = TRUE, color = "black", fill = "gray70")
          else if (method == "GAM")
            geom_smooth(aes(group = 1), method = "gam", formula = y ~ s(x),
                        se = TRUE, color = "black", fill = "gray70")
        } +
        facet_wrap(~ Size, scales = "free_y",
                   labeller = labeller(Size = function(x) labels10[x])) +
        scale_y_log10(
          breaks = scales::trans_breaks("log10", function(x) 10^x,   n = 5),    
          labels = scales::trans_format("log10", scales::math_format(10^.x))
        ) +
        labs(
          title = paste0(method, " of ", var, " vs nM\u2081\u2080"),
          x     = var,
          y     = expression(nM[10])
        ) +
        theme_bw(base_size = 14)
      
      # build the nM15 plot
      df15   <- res[["nM15"]]$data
      labels15 <- res[["nM15"]]$labels
      p15 <- ggplot(df15, aes(x = X, y = nM)) +
        geom_point(aes(color = Location), size = 3) +
        {
          if (method %in% c("Spearman","Pearson"))
            geom_smooth(aes(group = 1), method = "lm",  formula = y ~ x,
                        se = TRUE, color = "black", fill = "gray70")
          else if (method == "Quadratic Fit")
            geom_smooth(aes(group = 1), method = "lm",  formula = y ~ poly(x,2),
                        se = TRUE, color = "black", fill = "gray70")
          else if (method == "GAM")
            geom_smooth(aes(group = 1), method = "gam", formula = y ~ s(x),
                        se = TRUE, color = "black", fill = "gray70")
        } +
        facet_wrap(~ Size, scales = "free_y",
                   labeller = labeller(Size = function(x) labels15[x])) +
        scale_y_log10(
          breaks = scales::trans_breaks("log10", function(x) 10^x,   n = 5),    
          labels = scales::trans_format("log10", scales::math_format(10^.x))
        ) +
        labs(
          title = paste0(method, " of ", var, " vs nM\u2081\u2085"),
          x     = var,
          y     = expression(nM[15])
        ) +
        theme_bw(base_size = 14)
      
      # compose and save
      combined <- p10 / p15 + plot_layout(heights = c(1,1))
      ggsave(file, combined,
             width  = 16,    # inches
             height = 16,    # enough to hold both plots
             dpi    = 600)
    }
  )
  
  output$download_plot_nM10 <- downloadHandler(
    filename = function() {
      paste0("Correlation_", input$analysis_method, "_", input$spearman_var,
             "_nM10_", Sys.Date(), ".png")
    },
    content = function(file) {
      res     <- analysis_results(); req(res)
      df      <- res[["nM10"]]$data
      labels  <- res[["nM10"]]$labels
      method  <- input$analysis_method
      
      p10 <- ggplot(df, aes(x = X, y = nM)) +
        geom_point(aes(color = Location), size = 3) +
        {
          if (method %in% c("Spearman", "Pearson")) {
            geom_smooth(aes(group = 1),
                        method  = "lm",
                        formula = y ~ x,
                        se      = TRUE,
                        color   = "black",
                        fill    = "gray70")
          } else if (method == "Quadratic Fit") {
            geom_smooth(aes(group = 1),
                        method  = "lm",
                        formula = y ~ poly(x,2),
                        se      = TRUE,
                        color   = "black",
                        fill    = "gray70")
          } else if (method == "GAM") {
            geom_smooth(aes(group = 1),
                        method  = "gam",
                        formula = y ~ s(x),
                        se      = TRUE,
                        color   = "black",
                        fill    = "gray70")
          }
        } +
        facet_wrap(~ Size, scales = "free_y",
                   labeller = labeller(Size = function(x) labels[x])) +
        scale_y_log10(
          breaks = scales::trans_breaks("log10", function(x) 10^x,   n = 5),    
          labels = scales::trans_format("log10", scales::math_format(10^.x))
        ) +
        labs(
          title = paste0(method, " of ", input$spearman_var, " vs nM\u2081\u2080"),
          x     = input$spearman_var,
          y     = expression(nM[10])
        ) +
        theme_bw(base_size = 14)
      
      # save as high-res PNG
      ggsave(file, plot = p10,
             width  = 8,
             height = 8,
             dpi    = 600)
    }
  )
  
  # Download for nM15 plot
  output$download_plot_nM15 <- downloadHandler(
    filename = function() {
      paste0("Correlation_", input$analysis_method, "_", input$spearman_var,
             "_nM15_", Sys.Date(), ".png")
    },
    content = function(file) {
      res     <- analysis_results(); req(res)
      df      <- res[["nM15"]]$data
      labels  <- res[["nM15"]]$labels
      method  <- input$analysis_method
      
      p15 <- ggplot(df, aes(x = X, y = nM)) +
        geom_point(aes(color = Location), size = 3) +
        {
          if (method %in% c("Spearman", "Pearson")) {
            geom_smooth(aes(group = 1),
                        method  = "lm",
                        formula = y ~ x,
                        se      = TRUE,
                        color   = "black",
                        fill    = "gray70")
          } else if (method == "Quadratic Fit") {
            geom_smooth(aes(group = 1),
                        method  = "lm",
                        formula = y ~ poly(x,2),
                        se      = TRUE,
                        color   = "black",
                        fill    = "gray70")
          } else if (method == "GAM") {
            geom_smooth(aes(group = 1),
                        method  = "gam",
                        formula = y ~ s(x),
                        se      = TRUE,
                        color   = "black",
                        fill    = "gray70")
          }
        } +
        facet_wrap(~ Size, scales = "free_y",
                   labeller = labeller(Size = function(x) labels[x])) +
        scale_y_log10(
          breaks = scales::trans_breaks("log10", function(x) 10^x,   n = 5),    
          labels = scales::trans_format("log10", scales::math_format(10^.x))
        ) +
        labs(
          title = paste0(method, " of ", input$spearman_var, " vs nM\u2081\u2085"),
          x     = input$spearman_var,
          y     = expression(nM[15])
        ) +
        theme_bw(base_size = 14)
      
      ggsave(file, plot = p15,
             width  = 8,
             height = 8,
             dpi    = 600)
    }
  )
}

shinyApp(ui, server)