// NexusHV Terraform Provider — Skeleton
// Build: go build -o terraform-provider-nexushv
// Install: mv terraform-provider-nexushv ~/.terraform.d/plugins/

package main

import (
	"github.com/hashicorp/terraform-plugin-sdk/v2/helper/schema"
	"github.com/hashicorp/terraform-plugin-sdk/v2/plugin"
)

func main() {
	plugin.Serve(&plugin.ServeOpts{
		ProviderFunc: func() *schema.Provider {
			return &schema.Provider{
				Schema: map[string]*schema.Schema{
					"endpoint": {Type: schema.TypeString, Required: true, Description: "NexusHV API endpoint URL"},
					"username": {Type: schema.TypeString, Required: true},
					"password": {Type: schema.TypeString, Required: true, Sensitive: true},
				},
				ResourcesMap: map[string]*schema.Resource{
					"nexushv_vm":                resourceVM(),
					"nexushv_storage_container": resourceStorageContainer(),
					"nexushv_snapshot_policy":   resourceSnapshotPolicy(),
					"nexushv_network_policy":    resourceNetworkPolicy(),
				},
				DataSourcesMap: map[string]*schema.Resource{
					"nexushv_vms":      dataSourceVMs(),
					"nexushv_hosts":    dataSourceHosts(),
					"nexushv_storage":  dataSourceStorage(),
					"nexushv_networks": dataSourceNetworks(),
				},
			}
		},
	})
}

// Resource stubs — implement with NexusHV REST API calls
func resourceVM() *schema.Resource {
	return &schema.Resource{
		Create: nil, Read: nil, Update: nil, Delete: nil,
		Schema: map[string]*schema.Schema{
			"name":     {Type: schema.TypeString, Required: true},
			"cpu":      {Type: schema.TypeInt, Required: true},
			"ram_gb":   {Type: schema.TypeInt, Required: true},
			"disk_gb":  {Type: schema.TypeInt, Required: true},
			"os":       {Type: schema.TypeString, Optional: true, Default: "ubuntu22.04"},
			"template": {Type: schema.TypeString, Optional: true},
			"tags":     {Type: schema.TypeList, Optional: true, Elem: &schema.Schema{Type: schema.TypeString}},
		},
	}
}

func resourceStorageContainer() *schema.Resource { return &schema.Resource{} }
func resourceSnapshotPolicy() *schema.Resource   { return &schema.Resource{} }
func resourceNetworkPolicy() *schema.Resource    { return &schema.Resource{} }
func dataSourceVMs() *schema.Resource            { return &schema.Resource{} }
func dataSourceHosts() *schema.Resource          { return &schema.Resource{} }
func dataSourceStorage() *schema.Resource        { return &schema.Resource{} }
func dataSourceNetworks() *schema.Resource       { return &schema.Resource{} }
