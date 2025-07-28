#!/bin/bash

# Avvia R portabile con la tua Shiny app sulla porta 5750
./R-portable/bin/Rscript -e "shiny::runApp('app', launch.browser = FALSE, port = 5750)" &

# Attendi che R si avvii (aumenta se serve)
sleep 4

# Avvia Electron
./node_modules/.bin/electron .