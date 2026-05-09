# Wardrive Pi Image Builder
# Uses pi-gen via Docker. Requires: docker, git.
#
# Usage:
#   make build          — build the image (takes 20-40 min first run)
#   make clean          — remove pi-gen build artifacts
#   make flash DEV=/dev/sdX — flash the image to a card (USE WITH CARE)

PIGEN_DIR   := $(CURDIR)/pi-gen
DEPLOY_DIR  := $(PIGEN_DIR)/deploy
IMAGE_NAME  := wardrive-pi
PIGEN_REPO  := https://github.com/RPi-Distro/pi-gen.git
PIGEN_BRANCH := arm64

.PHONY: build clean flash clone-pigen

build: clone-pigen
	@echo "Building wardrive Pi image (Pi 3B, 64-bit Raspberry Pi OS Lite)..."
	# Copy our custom stage into pi-gen
	cp -r $(CURDIR)/stage-wardrive $(PIGEN_DIR)/stage-wardrive
	# Write config
	cat > $(PIGEN_DIR)/config << 'CONFIG'
IMG_NAME="$(IMAGE_NAME)"
RELEASE=bookworm
DEPLOY_COMPRESSION=xz
LOCALE_DEFAULT=en_US.UTF-8
TARGET_HOSTNAME=wardrive
KEYBOARD_LAYOUT="English (US)"
TIMEZONE_DEFAULT=America/New_York
FIRST_USER_NAME=wardrive
FIRST_USER_PASS=wardrive
ENABLE_SSH=1
STAGE_LIST="stage0 stage1 stage2 stage-wardrive"
CONFIG
	# Run the Docker builder
	cd $(PIGEN_DIR) && ./build-docker.sh
	@echo ""
	@echo "Image ready: $(DEPLOY_DIR)/$(IMAGE_NAME)*.img.xz"

clone-pigen:
	@if [ ! -d "$(PIGEN_DIR)" ]; then \
		git clone --depth=1 -b $(PIGEN_BRANCH) $(PIGEN_REPO) $(PIGEN_DIR); \
	fi

clean:
	rm -rf $(PIGEN_DIR)/work $(PIGEN_DIR)/deploy
	@echo "Build artifacts removed."

flash:
	@if [ -z "$(DEV)" ]; then echo "Usage: make flash DEV=/dev/sdX"; exit 1; fi
	@echo "Flashing to $(DEV) — THIS WILL ERASE THE DEVICE!"
	@read -p "Press Enter to continue or Ctrl-C to cancel..." _
	xz -dc $(DEPLOY_DIR)/$(IMAGE_NAME)*.img.xz | sudo dd of=$(DEV) bs=4M status=progress conv=fsync
	@echo "Done. Eject and insert into the Pi."
